"""Plivo/Twilio <-> OpenAI Realtime audio bridge (the voice channel's hot path).

Plivo or Twilio (see channels/voice/failover.py) opens a bidirectional
WebSocket to ``/voice/stream`` (see ``channels/voice/webhook.py``) and
streams the caller's audio as base64 mu-law (8 kHz). This endpoint opens a
second WebSocket to the OpenAI Realtime API and runs two concurrent pumps:

  * caller -> OpenAI : caller audio -> ``input_audio_buffer.append``
  * OpenAI -> caller : model audio -> a provider-specific play event, plus
                       queued (never interrupted) turn-taking, tool calls,
                       and transcript persistence (see ``voice.adapter``,
                       which owns the per-provider message-shape
                       differences).

Audio is mu-law 8 kHz on all three legs (Plivo ``contentType=audio/x-mulaw;
rate=8000``, Twilio's default Media Streams codec, and OpenAI ``g711_ulaw``)
so no resampling is required — the format-mismatch pitfall in
longrunning/operations/pitfalls.md is avoided by construction.

Twilio side is unverified against a real account — see
channels/voice/twilio_client.py's module docstring.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import structlog
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from apps.api.channels.voice import adapter as voice_adapter
from apps.api.channels.voice import call_control, compliance, metering, recovery, turn_controller
from apps.api.channels.voice.compliance import RecordingPolicy, resolve_recording_policy
from apps.api.channels.voice.greeting import effective_brand, greeting_cache, greeting_instruction
from apps.api.channels.voice.latency import LatencyTracker
from apps.api.channels.voice.voice_profile import (
    ResolvedVoice,
    resolve_profile,
    speaking_style_enabled,
)
from apps.api.channels.voice.turn_controller import LiveTranscriber
from apps.api.config import settings
from apps.api.core.prompts import (
    OUTBOUND_CALL_PROMPT,
    campaign_qualification_append,
    current_datetime_block,
    render_voice_append,
)
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.core.org_social_links import social_links_prompt_block
from apps.api.core.usage import deterministic_event_id, record_usage
from apps.api.core.whatsapp_assets import asset_catalog_prompt_block
from apps.api.core.whatsapp_template_catalog import (
    template_catalog_prompt_block as wa_template_catalog_prompt_block,
)
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.org import Org
from apps.api.db.models.script import Script
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["voice"])

_OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"

# How many times to reconnect the OpenAI leg after it drops on its own mid-call
# before giving up and ending the call. Bounded so a persistent outage (bad
# key, OpenAI down) doesn't loop forever billing/holding a live phone call —
# see the reconnect loop in voice_stream below.
_MAX_OAI_RECONNECTS = 8


async def _usage_checkpoint_loop(
    state: "voice_adapter.CallState", org_id: UUID, call_uuid: str, provider: str, started: float
) -> None:
    """Write the call's measured usage to the ledger every VOICE_USAGE_CHECKPOINT_SECONDS, so a crash costs
    at most one interval of it. Idempotent deltas (see metering.py); never raises into the call."""
    interval = call_control.bounded(settings.voice_usage_checkpoint_seconds, 15.0, 600.0, 60.0)
    try:
        while True:
            await asyncio.sleep(interval)
            await metering.checkpoint_call_usage(
                AsyncSessionLocal, org_id=org_id, call_uuid=call_uuid, provider=provider,
                usage=state.usage, call_seconds=time.monotonic() - started,
            )
    except asyncio.CancelledError:
        raise


def reconnect_exhausted(attempts: int, episode_age_s: float) -> bool:
    """Stop trying to reconnect the Realtime session? Always after ``_MAX_OAI_RECONNECTS`` attempts; with
    VOICE_RECONNECT_RECOVERY also once the current outage has lasted longer than its (clamped) time budget,
    so a caller is never kept waiting through many slow attempts."""
    if attempts > _MAX_OAI_RECONNECTS:
        return True
    if settings.voice_reconnect_recovery:
        return episode_age_s > call_control.bounded(settings.voice_reconnect_budget_seconds, 5.0, 300.0, 45.0)
    return False


async def _connect_to_openai(org_id: UUID | None = None) -> Any:
    """Open the raw OpenAI Realtime WebSocket — no instructions sent yet.

    websockets.connect(...) returns its own awaitable ``Connect`` object,
    not a plain coroutine — asyncio.create_task() requires an actual
    coroutine, hence this wrapper rather than passing it directly.

    Uses ``org_id``'s own OpenAI key (core/org_openai_key.py) when it has
    one configured, falling back to the platform's shared
    ``settings.openai_api_key`` otherwise — same as every other OpenAI call
    site. ``org_id=None`` (org not yet resolved at this call site) always
    uses the platform key.
    """
    api_key = settings.openai_api_key
    if org_id is not None:
        async with AsyncSessionLocal() as db:
            api_key = resolve_openai_api_key(await db.get(Org, org_id))
    return await websockets.connect(
        f"{_OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}",
        additional_headers={"Authorization": f"Bearer {api_key}"},
        max_size=None,
        # Explicit rather than relying on library defaults, so a dropped
        # connection is detected (and this leg's pump ends, per the
        # try/except around it above) within a bounded, documented time
        # instead of however the installed websockets version happens to
        # default it.
        ping_interval=20,
        ping_timeout=20,
    )


# Pre-connected OpenAI sessions started at answer()-time (webhook.py), keyed
# by call_uuid, claimed here once the media stream actually connects a
# moment later — see start_precall_connect/claim_precall_connect below.
_PRECALL_TIMEOUT_SECS = 20
_pending_precalls: dict[str, asyncio.Task[Any]] = {}
# The org each pre-connected session was prepared for (its script, key and tools). A session is only
# ever handed to a call that resolves to the SAME org; a mismatch is discarded, never reused.
_pending_precall_orgs: dict[str, UUID] = {}


def start_precall_connect(
    call_uuid: str,
    campaign_target_id: UUID | None,
    org_id: UUID | None,
    remote_number: str | None = None,
) -> None:
    """Kick off the OpenAI connect + session.update while Plivo/Twilio is
    still setting up the call (webhook.py's ``answer``), well before the
    media stream WebSocket connects and lands in ``voice_stream`` below.

    Also called for outbound calls right after they're placed (see
    ``routers/admin.py``'s ``outbound_call`` and ``workers/campaign_dialer.
    py``'s ``_dial_one``), keyed by the provider's own call-request id
    (Plivo ``request_uuid`` / Twilio ``sid``) — Twilio guarantees that id is
    the same ``CallSid`` the answer webhook later reports, so that whole
    ring duration becomes head start instead of just the post-answer gap.
    Plivo's ``request_uuid`` isn't documented as always equal to the later
    ``CallUUID``, so the idempotency guard below is what makes this safe to
    try anyway: if it doesn't match, this entry just self-expires unclaimed
    and ``answer()``'s own call starts a fresh one exactly as before.

    Best-effort and purely additive: if this is never claimed (the call
    never reaches the media stream — declined, dropped, provider error) the
    pending connection self-expires and closes after
    ``_PRECALL_TIMEOUT_SECS`` so it doesn't leak an open, billed OpenAI
    session. ``voice_stream`` falls back to connecting fresh, exactly as
    before this existed, whenever nothing was claimed.
    """
    if not call_uuid:
        return
    if call_uuid in _pending_precalls:
        # Already have one in flight for this id (e.g. the dial-time call
        # below and answer()'s own call landed on the same id, which is the
        # normal/expected case for Twilio) — don't clobber it with a second
        # OpenAI connection.
        return

    async def _prepare() -> tuple[Any, str]:
        resolved_org_id = org_id or UUID(settings.default_org_id)
        instructions = await _system_instructions(
            campaign_target_id, resolved_org_id, remote_number=remote_number
        )
        oai = await _connect_to_openai(resolved_org_id)
        _brand, policy = await _call_context(resolved_org_id, remote_number)
        await oai.send(
            json.dumps(_session_update_event(instructions, consent_tool=policy.mode == "consent"))
        )
        if settings.voice_greeting_audio_cache:
            # Warm the greeting audio while the phone is still ringing so the
            # first cached call after a deploy is already a hit.
            greeting = await _load_org_greeting(resolved_org_id)
            if greeting:
                greeting_cache.ensure_in_background(
                    resolved_org_id, greeting, await _load_org_voice(resolved_org_id)
                )
        return oai, instructions

    task: asyncio.Task[Any] = asyncio.create_task(_prepare())
    _pending_precalls[call_uuid] = task
    _pending_precall_orgs[call_uuid] = org_id or UUID(settings.default_org_id)

    async def _expire() -> None:
        await asyncio.sleep(_PRECALL_TIMEOUT_SECS)
        if _pending_precalls.get(call_uuid) is not task:
            return  # already claimed (and popped) or superseded
        _pending_precalls.pop(call_uuid, None)
        _pending_precall_orgs.pop(call_uuid, None)
        if task.done() and not task.cancelled() and task.exception() is None:
            oai, _instructions = task.result()
            await oai.close()
        else:
            task.cancel()

    asyncio.create_task(_expire())


async def claim_precall_connect(
    call_uuid: str, expected_org_id: UUID | None = None
) -> tuple[Any, str] | None:
    """Pop and await the pre-started OpenAI connection for this call, if
    ``start_precall_connect`` was called for it. Returns ``None`` if there
    isn't one (not a campaign/admin-placed call, already expired, or a
    connect failure) — caller falls back to connecting fresh."""
    if not call_uuid:
        return None
    task = _pending_precalls.pop(call_uuid, None)
    prepared_for = _pending_precall_orgs.pop(call_uuid, None)
    if task is None:
        return None
    try:
        claimed = await task
    except Exception:  # noqa: BLE001
        return None
    if expected_org_id is not None and prepared_for is not None and prepared_for != expected_org_id:
        # Prepared for another org (its script, its key): never reuse it for this call.
        logger.warning("voice_precall_org_mismatch_discarded", call_uuid=call_uuid)
        try:
            await claimed[0].close()
        except Exception:  # noqa: BLE001
            pass
        return None
    return claimed


async def _load_org_greeting(org_id: UUID) -> str | None:
    """The org's custom opening line (``Org.voice_greeting``), or None for the
    default greeting. A lookup failure means the default greeting, never a
    failed call."""
    try:
        async with AsyncSessionLocal() as db:
            org = await db.get(Org, org_id)
        return (org.voice_greeting or None) if org is not None else None
    except Exception:  # noqa: BLE001
        logger.warning("voice_greeting_lookup_failed", org_id=str(org_id), exc_info=True)
        return None


async def _load_org_voice(org_id: UUID) -> ResolvedVoice | None:
    """This org's voice profile merged over the platform settings, or None
    (NULL profile = the unchanged platform voice). Resolved per call from the
    call's own org; a lookup failure means no profile, never a failed call."""
    try:
        async with AsyncSessionLocal() as db:
            org = await db.get(Org, org_id)
        return resolve_profile(org.voice_profile) if org is not None else None
    except Exception:  # noqa: BLE001
        logger.warning("voice_profile_lookup_failed", org_id=str(org_id), exc_info=True)
        return None


async def _call_context(org_id: UUID | None, remote_number: str | None) -> tuple[str, RecordingPolicy]:
    """(spoken brand, recording policy) for this org and the person on the other end. A lookup failure
    means the legacy brand and the legacy policy, never a failed call."""
    org = None
    try:
        if org_id is not None:
            async with AsyncSessionLocal() as db:
                org = await db.get(Org, org_id)
    except Exception:  # noqa: BLE001
        logger.warning("voice_call_context_lookup_failed", org_id=str(org_id), exc_info=True)
    return effective_brand(org), resolve_recording_policy(
        org.voice_compliance if org is not None else None, remote_number
    )


async def _resolve_org_id(campaign_target_id: UUID | None, raw_org_id: str | None) -> UUID | None:
    """The org this call's usage/transcripts should be attributed to.

    A campaign-driven call's org comes from the campaign itself (authoritative
    regardless of any org_id query param) — this is what makes
    ``workers/campaign_dialer.py``'s per-org plan-limit gate and this call's
    actual recorded usage agree on the same org. A dashboard-placed single
    call (``routers/admin.py``'s ``outbound_call``) has no campaign, so it
    carries its org explicitly via ``?org_id=`` on the answer_url instead.
    """
    if campaign_target_id is not None:
        async with AsyncSessionLocal() as db:
            target = await db.get(CampaignTarget, campaign_target_id)
            if target is not None:
                campaign = await db.get(CallCampaign, target.campaign_id)
                if campaign is not None:
                    return campaign.org_id
    return UUID(raw_org_id) if raw_org_id else None


async def _system_instructions(
    campaign_target_id: UUID | None, org_id: UUID | None, remote_number: str | None = None
) -> str:
    """Base script, in order: the campaign's own pinned script (Script via
    CallCampaign.script_id), else the org's default script (Script with
    is_default=True), else the fixed appointment-booking script used by the
    single-number Dial page. Voice-only — WhatsApp still reads Org.script
    (see core/agent.py::_system_prompt_for), unrelated to this table (see
    db/models/script.py).

    A campaign-driven call layers its qualification criteria on top of that
    base as an addendum (see ``campaign_qualification_append``), rather than
    replacing it — the resolved script must still be followed on a campaign
    call, not silently swapped out for a generic one just because the call
    came from a campaign.
    """
    async with AsyncSessionLocal() as db:
        campaign = None
        if campaign_target_id is not None:
            target = await db.get(CampaignTarget, campaign_target_id)
            campaign = await db.get(CallCampaign, target.campaign_id) if target else None

        base = OUTBOUND_CALL_PROMPT
        if campaign is not None and campaign.script_id is not None:
            script = await db.get(Script, campaign.script_id)
            if script is not None:
                base = script.content
        elif org_id is not None:
            result = await db.execute(
                select(Script).where(Script.org_id == org_id, Script.is_default.is_(True)).limit(1)
            )
            default_script = result.scalar_one_or_none()
            if default_script is not None:
                base = default_script.content

        catalog = (
            await asset_catalog_prompt_block(db, org_id) if org_id is not None else ""
        )
        template_catalog = (
            await wa_template_catalog_prompt_block(db, org_id) if org_id is not None else ""
        )
        social_links = await social_links_prompt_block(db)
        org_row = await db.get(Org, org_id) if org_id is not None else None
        speaking_style = bool(org_row is not None and speaking_style_enabled(org_row.voice_profile))

    policy = resolve_recording_policy(
        org_row.voice_compliance if org_row is not None else None, remote_number
    )
    voice_append = render_voice_append(
        effective_brand(org_row),
        ai_disclosure=settings.voice_greeting_ai_disclosure,
        speaking_style=speaking_style,
    )
    tail = f"{current_datetime_block()}\n\n{voice_append.strip()}"
    if catalog:
        tail = f"{tail}\n\n{catalog}"
    if template_catalog:
        tail = f"{tail}\n\n{template_catalog}"
    if social_links:
        tail = f"{tail}\n\n{social_links}"
    if policy.mode == "consent":
        tail = f"{tail}\n\n{compliance.CONSENT_PROMPT}"

    if campaign is not None:
        return (
            f"{base.strip()}\n\n"
            f"{campaign_qualification_append(campaign.criteria).strip()}\n\n"
            f"{tail}"
        )

    return f"{base.strip()}\n\n{tail}"


def _session_update_event(instructions: str, *, consent_tool: bool = False) -> dict[str, Any]:
    """Initial session config: mu-law I/O, semantic VAD, shared tool schemas.

    GA Realtime API shape (the beta shape — flat input_audio_format /
    output_audio_format / top-level voice / temperature — was removed on
    2026-05-12 and now fails the connection with beta_api_shape_disabled).
    """
    session: dict[str, Any] = {
        "type": "realtime",
        "instructions": instructions,
        "audio": {
            "input": {
                "format": {"type": "audio/pcmu"},
                # semantic_vad (not server_vad's fixed threshold/silence
                # timer) decides turn-end from the *content* of what was
                # said, not just when the caller goes quiet. That's what
                # stops a short backchannel ("ok", "haan", "theek hai")
                # from being treated as the caller taking the turn and
                # barging in on the agent's current response — server_vad
                # can't tell those apart from a real interruption since
                # both look identical at the moment speech starts.
                # eagerness "medium" (was "low") trims the wait before the
                # agent decides the caller is done talking, cutting response
                # latency at the cost of a somewhat higher chance of the
                # agent barging in on a caller who paused mid-sentence — see
                # CHANGES_voice_latency.md for the tradeoff writeup.
                # Turn end: server_vad with a short silence window, so the
                # reply starts ~0.4s after the caller stops (semantic_vad
                # could hold the turn open for seconds). The old reason for
                # semantic_vad — not treating "ok"/"haan" as a turn — is now
                # handled by adapter.py (early-cut timer + backchannel filter).
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": settings.voice_vad_silence_ms,
                    # Caller talking mid-answer no longer wipes the
                    # in-progress response — OpenAI keeps generating and
                    # speaking it to completion, and the caller's new speech
                    # is committed as the next turn once the current one's
                    # response.done arrives (queued, not interrupted). See
                    # adapter.py's input_audio_buffer.speech_started handler,
                    # which used to clear playback/cancel the response on
                    # every barge-in and no longer does.
                    # UNVERIFIED: interrupt_response is our best-guess field
                    # name for this GA session shape, not confirmed against a
                    # live connection — check session.updated doesn't error.
                    "interrupt_response": False,
                    # We create every response ourselves now (adapter.py's
                    # input_audio_buffer.committed handler) instead of
                    # letting the server auto-fire one the instant a turn
                    # commits — that's the only hook that lets us inject a
                    # one-off "you were just talked over, acknowledge it"
                    # instruction on the specific turn that follows an
                    # interruption, and a plain response.create the rest of
                    # the time otherwise.
                    "create_response": False,
                },
                "noise_reduction": {"type": "far_field"},
                # language="hi" is a bias hint, not a hard constraint - Whisper
                # still transcribes other languages when the caller clearly
                # speaks one. Without it, whisper-1 auto-detects the language
                # per utterance from audio alone, and short/accented Hindi
                # speech was being misheard as English words entirely (a
                # caller's Hindi reply showing up as "And hello" in transcripts)
                # - the language-matching logic below then dutifully "matched"
                # the caller's language based on that wrong transcript.
                "transcription": {"model": "whisper-1", "language": "hi"},
            },
            "output": {
                "format": {"type": "audio/pcmu"},
                "voice": settings.openai_realtime_voice,
            },
        },
        "tools": voice_adapter.realtime_tools(consent=consent_tool),
        "tool_choice": "auto",
    }
    if settings.voice_tts_provider == "elevenlabs":
        # Text-only output — OpenAI still does speech-to-text, reasoning,
        # tool-calling, turn detection, and barge-in; it just stops
        # synthesizing audio itself. adapter.handle_openai_event pipes the
        # resulting response.text.delta events through ElevenLabs' streaming
        # TTS instead (see elevenlabs_client.py).
        #
        # UNVERIFIED: `output_modalities` is our best-guess field name for
        # this GA session shape (mirroring the audio.input/audio.output
        # split above) — OpenAI's docs weren't checked against a live
        # connection for this. Confirm session.updated doesn't error before
        # relying on this in production.
        session["output_modalities"] = ["text"]
    return {"type": "session.update", "session": session}


async def _start_live_transcriber(
    state: "voice_adapter.CallState", org_id: UUID, call_ws: WebSocket, log: Any
) -> None:
    """Bring up the live partial-transcript connection in the background. If
    anything fails the call simply keeps the duration-based turn-taking."""
    try:
        async with AsyncSessionLocal() as db:
            state.openai_api_key = resolve_openai_api_key(await db.get(Org, org_id))

        async def on_delta(delta: str) -> None:
            # oai is bound by the time deltas flow: audio is only fed once the
            # call pumps run, which is after oai exists.
            await voice_adapter.handle_live_delta(delta, call_ws, state.oai_ws, state, log)

        asyncio.create_task(turn_controller.warm_up(state.openai_api_key))
        transcriber = LiveTranscriber(state.openai_api_key, on_delta, log)
        if await transcriber.start():
            state.live = transcriber
        else:
            await transcriber.close()
    except Exception as exc:  # noqa: BLE001 — must never affect the call
        log.warning("voice_live_transcriber_setup_failed", error=str(exc))


async def _stop_live_transcriber(state: "voice_adapter.CallState", start_task: Any) -> None:
    if start_task is not None and not start_task.done():
        start_task.cancel()
        await asyncio.gather(start_task, return_exceptions=True)
    live, state.live = state.live, None
    if live is not None:
        await live.close()


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """Bridge a single Plivo/Twilio call to an OpenAI Realtime session."""
    await ws.accept()

    caller = ws.query_params.get("from", "unknown")
    call_uuid = ws.query_params.get("call_uuid", "")
    provider = ws.query_params.get("provider", "plivo")
    raw_campaign_target_id = ws.query_params.get("campaign_target_id")
    raw_org_id = ws.query_params.get("org_id")

    # Best case: webhook.py's answer() already started (and by now likely
    # finished) connecting to OpenAI for this exact call_uuid, well before
    # this media stream even opened — claim it instead of connecting fresh.
    # Only works here for Plivo, which preserves the query string (Twilio
    # doesn't; its call_uuid isn't known until the handshake loop below, so
    # it gets a second claim attempt there instead).
    expected_org: UUID | None = None
    if call_uuid:
        try:
            expected_org = await _resolve_org_id(
                UUID(raw_campaign_target_id) if raw_campaign_target_id else None, raw_org_id
            ) or UUID(settings.default_org_id)
        except Exception:  # noqa: BLE001 - claim without the check rather than lose the head start
            expected_org = None
    precall = await claim_precall_connect(call_uuid, expected_org) if call_uuid else None

    # Fallback: nothing pre-connected (not a campaign/admin call, Twilio,
    # already expired, or a connect failure) — start fresh now, still
    # overlapping with the Plivo/Twilio handshake + DB/org-resolution work
    # below rather than waiting for it, same as before this pre-connect
    # existed. Awaited once, right before it's actually needed, further down.
    # Also resolves the org's own OpenAI key (if any) concurrently, from
    # whatever org identity is already available on the query string —
    # Twilio's real org_id/campaign_target_id aren't known yet at this point
    # (see comment below), so a Twilio fallback connect still uses the
    # platform key; corrected the moment `pump_call_to_openai` resolves the
    # real org further down would require a second connection, so this is
    # accepted as a documented gap rather than reconnecting mid-call.
    async def _connect_fallback() -> Any:
        fallback_org_id = await _resolve_org_id(
            UUID(raw_campaign_target_id) if raw_campaign_target_id else None, raw_org_id
        )
        return await _connect_to_openai(fallback_org_id)

    oai_connect_task = None if precall else asyncio.create_task(_connect_fallback())

    # Twilio drops the Stream url's query string on connect (Plivo doesn't),
    # so the above are all still defaults for a Twilio call at this point —
    # its real values travel instead as <Parameter> tags on the TwiML (see
    # webhook.py's answer), delivered here in the "start" event's
    # start.customParameters. Peel off the handshake frames ("connected",
    # then "start") before doing any provider/org-dependent work so a Twilio
    # call picks up its actual identity too; a stream_id found here is handed
    # to state below so pump_call_to_openai's own "start" handling (which
    # still runs, for Plivo) is a harmless no-op repeat if it fires again.
    stream_id: str | None = None
    try:
        while True:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
            msg = json.loads(raw)
            event = msg.get("event")
            if event == "connected":
                continue
            if event == "start":
                start = msg.get("start") or {}
                stream_id = (
                    start.get("streamId")
                    or start.get("streamSid")
                    or msg.get("streamId")
                    or msg.get("streamSid")
                    or ""
                )
                custom = start.get("customParameters") or {}
                if custom:
                    caller = custom.get("from", caller)
                    call_uuid = custom.get("call_uuid", call_uuid)
                    provider = custom.get("provider", provider)
                    raw_org_id = custom.get("org_id", raw_org_id)
                    raw_campaign_target_id = custom.get(
                        "campaign_target_id", raw_campaign_target_id
                    )
            break
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass

    if precall is None and call_uuid:
        # Second attempt, now that Twilio's real call_uuid (delivered via
        # customParameters above, unavailable at the top for Twilio since
        # it drops the query string) is known. A no-op for Plivo, which
        # already succeeded above if there was anything to claim.
        precall = await claim_precall_connect(
            call_uuid,
            (
                await _resolve_org_id(
                    UUID(raw_campaign_target_id) if raw_campaign_target_id else None, raw_org_id
                )
                or UUID(settings.default_org_id)
            ),
        )
        if precall is not None and oai_connect_task is not None:
            oai_connect_task.cancel()
            oai_connect_task = None

    campaign_target_id = UUID(raw_campaign_target_id) if raw_campaign_target_id else None
    org_id = await _resolve_org_id(campaign_target_id, raw_org_id)
    log = logger.bind(caller=caller, call_uuid=call_uuid, provider=provider)
    log.info("voice_stream_connected")
    # Wall-clock reference for every close/error log below, so a dropped
    # call is immediately attributable to (or ruled out as) a duration cap —
    # e.g. the Realtime API's session-length limit — from the log line
    # alone, without cross-referencing a separate start timestamp.
    call_started = time.monotonic()

    # Same fallback used for state.org_id below (billing/leads/conversation
    # attribution) — _system_instructions must resolve the org's script
    # against this SAME id, not the raw pre-fallback one, or an inbound call
    # with no matching dedicated number gets correctly attributed to the
    # default org everywhere else while silently getting the platform
    # default script instead of that org's own.
    resolved_org_id = org_id or UUID(settings.default_org_id)

    conversation_id: Any = None
    call_state: Any = None  # for the usage flush in the outer finally
    usage_task: Any = None
    try:
        user_id, conversation_id = await voice_adapter.open_voice_conversation(
            caller, call_uuid, org_id=org_id
        )
        state = voice_adapter.CallState(
            user_id=user_id,
            conversation_id=conversation_id,
            org_id=resolved_org_id,
            provider=provider,
            campaign_target_id=campaign_target_id,
            tts_provider=settings.voice_tts_provider,
        )
        if stream_id:
            state.stream_id = stream_id
        state.call_uuid = call_uuid or None
        state.call_ws = ws
        call_state = state
        if settings.voice_metering_v2 and call_uuid:
            usage_task = asyncio.create_task(
                _usage_checkpoint_loop(state, resolved_org_id, call_uuid, provider, call_started)
            )
        brand, policy = await _call_context(resolved_org_id, caller)
        state.consent_tool = policy.mode == "consent"
        state.latency = LatencyTracker(
            org_id=resolved_org_id, call_uuid=call_uuid or None, provider=provider
        )
        # Fetched alongside the OpenAI setup, awaited only when the greeting is sent.
        greeting_task = asyncio.create_task(_load_org_greeting(resolved_org_id))
        voice_task = asyncio.create_task(_load_org_voice(resolved_org_id))
        live_start_task = (
            asyncio.create_task(_start_live_transcriber(state, resolved_org_id, ws, log))
            if settings.voice_live_turn_detection
            else None
        )
        if campaign_target_id is not None:
            # Proof the call actually connected — tells the hangup webhook
            # (campaign_dialer.handle_call_ended) not to re-dial this person
            # just because qualify_lead didn't fire before they hung up.
            await voice_adapter.attach_campaign_conversation(campaign_target_id, conversation_id)

        if precall is not None:
            # Already connected AND session.update already sent, back at
            # answer()-time — the biggest possible head start, overlapping
            # the OpenAI setup with however long Plivo/Twilio took to reach
            # us after answering, not just the DB/org-resolution work above.
            oai, instructions = precall
        else:
            instructions = await _system_instructions(
                campaign_target_id, resolved_org_id, remote_number=caller
            )
            # Usually already connected by now regardless — the handshake
            # ran concurrently with the work above instead of after it.
            oai = await oai_connect_task
        state.base_instructions = instructions
        state.oai_ws = oai

        try:
            session_update_needed = precall is None
            if precall is not None and state.consent_tool and compliance.CONSENT_PROMPT not in instructions:
                # The pre-connected session was prepared before this caller's region was known; this
                # call needs the consent question and tool, so refresh the session.
                instructions = f"{instructions}\n\n{compliance.CONSENT_PROMPT}"
                state.base_instructions = instructions
                session_update_needed = True
            if session_update_needed:
                await oai.send(
                    json.dumps(_session_update_event(instructions, consent_tool=state.consent_tool))
                )
            # Make the agent greet first instead of waiting for the caller.
            # Protect this greeting from being wiped by the callee's
            # reflexive "hello?" on an outbound call - adapter.handle_openai_
            # event suppresses barge-in until this deadline (or until the
            # greeting's response.done arrives, whichever comes first).
            org_greeting = await greeting_task
            state.voice = await voice_task
            played_cached = False
            notice = policy.spoken_notice
            if (
                org_greeting
                and not notice  # cached audio is fixed: it cannot carry a per-region notice
                and policy.mode != "consent"  # nor can it ask for consent; the model must speak first
                and settings.voice_greeting_audio_cache
                and state.tts_provider == "elevenlabs"
            ):
                cached = greeting_cache.get(resolved_org_id, org_greeting, state.voice)
                if cached is None:
                    greeting_cache.ensure_in_background(resolved_org_id, org_greeting, state.voice)
                else:
                    try:
                        await voice_adapter.play_cached_greeting(
                            ws, oai, state, cached.text, cached.chunks, log
                        )
                        played_cached = True
                    except Exception:  # noqa: BLE001
                        log.warning("voice_greeting_cached_failed", exc_info=True)
                        # Anything already sent can't be taken back: only fall
                        # back to the spoken greeting if the caller heard nothing.
                        played_cached = state.playback_end > 0
            if not played_cached:
                state.greeting_guard_until = time.monotonic() + 10.0
                state.latency.begin_turn(kind="greeting")
                state.latency.mark("response_requested")
                await oai.send(
                    json.dumps(
                        {
                            "type": "response.create",
                            "response": {
                                # Spelled out explicitly (not just "as your instructions
                                # describe") because this is the call's very first
                                # response - there's no conversation history yet for the
                                # model to fall back on. An org's own greeting is spoken
                                # verbatim; otherwise the default wording (see
                                # channels/voice/greeting.py).
                                "instructions": greeting_instruction(
                                    org_greeting,
                                    brand,
                                    settings.voice_greeting_ai_disclosure,
                                    notice=notice,
                                ),
                            },
                        }
                    )
                )
            log.info("openai_realtime_connected")

            async def pump_call_to_openai() -> None:
                try:
                    while True:
                        raw = await ws.receive_text()
                        msg = json.loads(raw)
                        event = msg.get("event")
                        if event == "media":
                            payload = (msg.get("media") or {}).get("payload")
                            if payload:
                                if state.live is not None:
                                    state.live.feed(payload)
                                    if state.live.healthy:
                                        state.usage.transcribe_bytes += len(payload) * 3 // 4
                                await oai.send(
                                    json.dumps(
                                        {
                                            "type": "input_audio_buffer.append",
                                            "audio": payload,
                                        }
                                    )
                                )
                        elif event == "start":
                            start = msg.get("start") or {}
                            # streamId (Plivo) / streamSid (Twilio) — Twilio
                            # requires this on every outbound media/clear
                            # message (see adapter.py's _send_media).
                            sid = (
                                start.get("streamId")
                                or start.get("streamSid")
                                or msg.get("streamId")
                                or msg.get("streamSid")
                                or ""
                            )
                            state.stream_id = sid
                            log.info("call_stream_start", stream_id=sid)
                        elif event == "connected":
                            # Twilio sends this before "start" — nothing to do.
                            pass
                        elif event == "stop":
                            log.info("call_stream_stop")
                            state.call_ended = True
                            break
                except WebSocketDisconnect:
                    log.info(
                        "call_ws_disconnected",
                        call_duration_s=round(time.monotonic() - call_started, 1),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "pump_call_error",
                        error=str(exc),
                        call_duration_s=round(time.monotonic() - call_started, 1),
                    )
                finally:
                    state.call_ended = True  # the caller's side is gone, however it ended
                    await oai.close()

            async def pump_openai_to_call() -> None:
                try:
                    async for raw in oai:
                        event = json.loads(raw)
                        try:
                            await voice_adapter.handle_openai_event(
                                event, ws, oai, state, log
                            )
                        except websockets.ConnectionClosed:
                            # The OpenAI socket itself died mid-handler (e.g. a
                            # send() raced a close) — this DOES end the call,
                            # let it propagate to the outer handler below.
                            raise
                        except Exception as exc:  # noqa: BLE001
                            # One malformed/racy event (a bad tool-call
                            # payload, a stray state-machine edge case) used
                            # to end the whole pump here, which tears down
                            # the live call for what is really just one bad
                            # turn. Drop the event, keep the call alive.
                            log.warning(
                                "voice_event_handler_error",
                                event_type=event.get("type"),
                                error=str(exc),
                                call_duration_s=round(time.monotonic() - call_started, 1),
                                exc_info=True,
                            )
                except websockets.ConnectionClosed as exc:
                    # If this fires around the same call_duration_s across
                    # multiple calls, that's a session-duration cap on the
                    # Realtime model, not a random drop — see this
                    # function's module docstring / the P1 note in the
                    # voice-call-drop investigation. close_code/close_reason
                    # are OpenAI's own WS close frame — the actual reason it
                    # dropped the session, not just that it happened.
                    # A close shortly after we sent response.cancel is the suspected
                    # failure mode: stop cancelling for the rest of this call.
                    voice_adapter.note_openai_closed(state, log)
                    log.info(
                        "openai_ws_closed",
                        call_duration_s=round(time.monotonic() - call_started, 1),
                        close_code=exc.code,
                        close_reason=exc.reason,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "pump_openai_error",
                        error=str(exc),
                        call_duration_s=round(time.monotonic() - call_started, 1),
                    )

            # The caller's own leg (call_task) runs for the life of the real
            # phone call and is the only thing allowed to end it. The OpenAI
            # leg (oai_task) is recreated in a loop: if the Realtime session
            # drops on its own mid-call (rate limit, transient error, etc. —
            # see openai_ws_closed's close_code/close_reason above) that must
            # NOT hang up on the caller. Reconnect a fresh session and keep
            # going instead — only the caller hanging up (call_task finishing)
            # ends the call.
            call_task = asyncio.create_task(pump_call_to_openai())
            oai_reconnects = 0
            episode_started = 0.0  # when the current run of failed reconnects began
            last_connected = time.monotonic()
            try:
                while True:
                    oai_task = asyncio.create_task(pump_openai_to_call())
                    done, _pending = await asyncio.wait(
                        {call_task, oai_task}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if call_task in done:
                        oai_task.cancel()
                        await asyncio.gather(oai_task, return_exceptions=True)
                        break
                    dropped_at = time.monotonic()  # the Realtime session just went away
                    if settings.voice_reconnect_recovery and time.monotonic() - last_connected > 30.0:
                        # The last session stayed healthy for a while: this is a fresh incident, not
                        # the same outage, so it gets its own retry allowance and time budget.
                        oai_reconnects = 0
                        episode_started = 0.0
                    oai_reconnects += 1
                    if not episode_started:
                        episode_started = time.monotonic()
                    if reconnect_exhausted(oai_reconnects, time.monotonic() - episode_started):
                        log.warning(
                            "openai_ws_reconnect_exhausted",
                            attempts=oai_reconnects,
                            call_duration_s=round(time.monotonic() - call_started, 1),
                        )
                        if settings.voice_reconnect_recovery:
                            # Without this the caller is left on an open, silent line (keepCallAlive).
                            await voice_adapter.play_fallback_apology(state, log)
                            try:
                                await voice_adapter._dispatch_deduped(
                                    state, "transfer_to_human",
                                    json.dumps({"reason": recovery.FOLLOW_UP_REASON, "urgency": "high"}), log,
                                )
                            except Exception:  # noqa: BLE001
                                log.warning("voice_reconnect_follow_up_failed", exc_info=True)
                            ending = call_control.request_hangup(
                                state, log, reason="realtime_unavailable",
                                outcome="realtime_unavailable", abortable=False,
                            )
                            if ending is not None:
                                await asyncio.wait({ending}, timeout=30)
                        call_task.cancel()
                        await asyncio.gather(call_task, return_exceptions=True)
                        break
                    log.warning(
                        "openai_ws_reconnecting",
                        attempt=oai_reconnects,
                        call_duration_s=round(time.monotonic() - call_started, 1),
                    )
                    await voice_adapter.cancel_pending_tasks(state)
                    voice_adapter.reset_after_reconnect(state)
                    try:
                        oai = await _connect_to_openai(resolved_org_id)
                        await oai.send(
                            json.dumps(
                                _session_update_event(state.base_instructions, consent_tool=state.consent_tool)
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("openai_ws_reconnect_failed", error=str(exc))
                        await asyncio.sleep(min(2**oai_reconnects, 8))
                        continue
                    # pump_call_to_openai and every handler below read `oai`
                    # as a free variable off this enclosing scope, so
                    # reassigning it here is what makes the still-running
                    # call_task (and the next handle_openai_event call) start
                    # using the new connection — no restart of call_task
                    # needed.
                    state.oai_ws = oai
                    last_connected = time.monotonic()
                    state.usage.reconnects += 1
                    state.latency.metric(
                        "reconnect_recovery_ms", (last_connected - dropped_at) * 1000, attempt=oai_reconnects
                    )
                    state.response_active = False
                    state.ack_stage = None
                    state.suppress_response_audio = False
                    state.reply_pending = False
                    # New session = no conversation history and nothing else
                    # will fire the next response on its own (unlike a normal
                    # turn, create_response=False means only we do that) — so
                    # without this the caller is just left in silence after a
                    # reconnect they can't see happened. Ask the model to own
                    # up to the gap and hand the turn back rather than
                    # guessing at content it no longer has.
                    try:
                        if settings.voice_reconnect_recovery:
                            # Give the new session the last few turns (text only, sensitive digits masked,
                            # no tool calls) and tell it what is already done, instead of making the
                            # caller repeat themselves.
                            try:
                                for item in await voice_adapter.build_reconnect_items(state):
                                    await oai.send(json.dumps(item))
                            except Exception:  # noqa: BLE001 - no context beats no reply
                                log.warning("voice_reconnect_context_failed", exc_info=True)
                            await oai.send(
                                json.dumps(
                                    {
                                        "type": "response.create",
                                        "response": {
                                            "instructions": (
                                                f"{state.base_instructions}\n\n"
                                                f"{voice_adapter.reconnect_resume_instruction(state)}"
                                            )
                                        },
                                    }
                                )
                            )
                            continue
                        await oai.send(
                            json.dumps(
                                {
                                    "type": "response.create",
                                    "response": {
                                        "instructions": (
                                            "The connection glitched for a moment. In "
                                            "Hindi, briefly and naturally apologize for "
                                            "the interruption and ask them to repeat what "
                                            "they were saying. Keep it short."
                                        ),
                                    },
                                }
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("openai_ws_reconnect_resume_failed", error=str(exc))
            finally:
                if not call_task.done():
                    call_task.cancel()
                await asyncio.gather(call_task, return_exceptions=True)
        finally:
            if not greeting_task.done():
                greeting_task.cancel()
            # Close anything this call left open: an ElevenLabs socket or
            # prewarm, a pending TTS finish, the silence / reply / live tasks.
            # Before this they simply outlived the call.
            try:
                call_control.cancel_pending_hangup(state)
                await voice_adapter.cancel_pending_tasks(state)
            except Exception:  # noqa: BLE001
                log.warning("voice_call_cleanup_failed", exc_info=True)
            await _stop_live_transcriber(state, live_start_task)
            # pump_call_to_openai already closes oai in its own finally on
            # the normal path — this is a safety net for the (unusual) case
            # where something above raised before the pumps ever started.
            await oai.close()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "voice_stream_error",
            error=str(exc),
            call_duration_s=round(time.monotonic() - call_started, 1),
        )
        await record_error()
    finally:
        # None when a precall connection was claimed (nothing of its own to
        # clean up here) or already cancelled after a second, successful
        # claim attempt above.
        if oai_connect_task is not None:
            if oai_connect_task.done():
                # Not consumed via `oai` above (an exception hit before that
                # line) — still close it so a fully-connected session isn't
                # left dangling. Safe/idempotent if it was already closed.
                if not oai_connect_task.cancelled() and oai_connect_task.exception() is None:
                    try:
                        await oai_connect_task.result().close()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                oai_connect_task.cancel()
        if conversation_id is not None:
            await voice_adapter.close_voice_conversation(
                conversation_id, campaign_target_id=campaign_target_id
            )
        call_duration_s = round(time.monotonic() - call_started, 1)
        log.info("voice_stream_ended", call_duration_s=call_duration_s)

        # Usage metering (req §6): connected voice seconds, keyed to this
        # call's own id so a reconnect/retry of the same call_uuid never
        # double-counts. Never raises past this point - a metering failure
        # must not mask (or re-trigger) call teardown.
        if usage_task is not None and not usage_task.done():
            usage_task.cancel()
            await asyncio.gather(usage_task, return_exceptions=True)
        if call_uuid:
            try:
                async with AsyncSessionLocal() as usage_db:
                    if settings.voice_metering_v2 and call_state is not None:
                        # Checkpointed scheme: whatever the periodic checkpoints did not cover (including
                        # the rest of the call's seconds) is written now, as the final delta.
                        await metering.flush_call_usage(
                            usage_db, org_id=resolved_org_id, call_uuid=call_uuid, provider=provider,
                            usage=call_state.usage, call_seconds=call_duration_s,
                        )
                        await usage_db.commit()
                        call_state.usage.confirm()
                    else:
                        await record_usage(
                            usage_db,
                            organization_id=resolved_org_id,
                            event_id=deterministic_event_id("voice_call_ended", call_uuid),
                            service="voice",
                            usage_type="voice_seconds",
                            quantity=call_duration_s,
                            unit="seconds",
                            provider=provider,
                            source="realtime_bridge",
                            request_id=call_uuid,
                        )
                        await usage_db.commit()
            except Exception:  # noqa: BLE001
                log.warning("usage_metering_failed", call_uuid=call_uuid)
