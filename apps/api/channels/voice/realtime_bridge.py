"""Plivo/Twilio <-> OpenAI Realtime audio bridge (the voice channel's hot path).

Plivo or Twilio (see channels/voice/failover.py) opens a bidirectional
WebSocket to ``/voice/stream`` (see ``channels/voice/webhook.py``) and
streams the caller's audio as base64 mu-law (8 kHz). This endpoint opens a
second WebSocket to the OpenAI Realtime API and runs two concurrent pumps:

  * caller -> OpenAI : caller audio -> ``input_audio_buffer.append``
  * OpenAI -> caller : model audio -> a provider-specific play event, plus
                       barge-in, tool calls, and transcript persistence
                       (see ``voice.adapter``, which owns the per-provider
                       message-shape differences).

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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from apps.api.channels.voice import adapter as voice_adapter
from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.channels.voice import twilio_client as voice_twilio
from apps.api.config import settings
from apps.api.core.prompts import (
    OUTBOUND_CALL_PROMPT,
    VOICE_APPEND,
    campaign_qualification_prompt,
    current_datetime_block,
)
from apps.api.core.usage import get_credit_usage
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.deps import is_over_plan_limit
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["voice"])

_OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime"


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


async def _system_instructions(campaign_target_id: UUID | None, org_id: UUID | None) -> str:
    """Pick the campaign-scoped qualification script when this call was
    placed by the campaign dialer, else the calling org's own script
    override, else the fixed appointment-booking script used by the
    single-number Dial page."""
    if campaign_target_id is not None:
        async with AsyncSessionLocal() as db:
            target = await db.get(CampaignTarget, campaign_target_id)
            campaign = await db.get(CallCampaign, target.campaign_id) if target else None
        if campaign is not None:
            return (
                f"{campaign_qualification_prompt(campaign.criteria).strip()}\n\n"
                f"{current_datetime_block()}\n\n{VOICE_APPEND.strip()}"
            )

    base = OUTBOUND_CALL_PROMPT
    if org_id is not None:
        async with AsyncSessionLocal() as db:
            org = await db.get(Org, org_id)
        if org is not None and org.script:
            base = org.script
    return f"{base.strip()}\n\n{current_datetime_block()}\n\n{VOICE_APPEND.strip()}"


def _session_update_event(instructions: str) -> dict[str, Any]:
    """Initial session config: mu-law I/O, server VAD, shared tool schemas.

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
                "turn_detection": {"type": "server_vad", "silence_duration_ms": 500},
                "transcription": {"model": "whisper-1"},
            },
            "output": {
                "format": {"type": "audio/pcmu"},
                "voice": settings.openai_realtime_voice,
            },
        },
        "tools": voice_adapter.realtime_tools(),
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


_USAGE_CHECK_INTERVAL_SECS = 20


async def _watch_usage_limit(
    org_id: UUID, call_started_at: datetime, oai: Any, call_uuid: str, provider: str, log: Any
) -> None:
    """Poll the org's plan usage while this call is live and force-hang-up
    the instant it crosses ``max_call_minutes``.

    ``core.usage.get_credit_usage`` only sums *ended* conversations, so it
    can't see this call's own in-progress duration — add elapsed wall-clock
    time for the current call on top of it, otherwise a single long call
    could run well past the limit before anything noticed.
    """
    log.info("voice_usage_watcher_started", org_id=str(org_id))
    while True:
        await asyncio.sleep(_USAGE_CHECK_INTERVAL_SECS)
        elapsed_minutes = (datetime.now(UTC) - call_started_at).total_seconds() / 60.0
        async with AsyncSessionLocal() as db:
            usage = await get_credit_usage(db, org_id)
            over_limit = await is_over_plan_limit(
                db, org_id, "max_call_minutes", usage.call_minutes + elapsed_minutes
            )
        log.info(
            "voice_usage_watcher_check",
            call_minutes=usage.call_minutes,
            elapsed_minutes=elapsed_minutes,
            over_limit=over_limit,
        )
        if over_limit:
            log.info("voice_call_limit_reached", call_uuid=call_uuid)
            try:
                await oai.send(
                    json.dumps(
                        {
                            "type": "response.create",
                            "response": {
                                "instructions": (
                                    "Apologize briefly, say you have to end the "
                                    "call now, then stop."
                                ),
                            },
                        }
                    )
                )
                await asyncio.sleep(4)
            except Exception:  # noqa: BLE001
                pass
            if provider == "twilio":
                await voice_twilio.hangup_call(call_uuid)
            else:
                await voice_plivo.hangup_call(call_uuid)
            return


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """Bridge a single Plivo/Twilio call to an OpenAI Realtime session."""
    await ws.accept()
    caller = ws.query_params.get("from", "unknown")
    call_uuid = ws.query_params.get("call_uuid", "")
    provider = ws.query_params.get("provider", "plivo")
    raw_campaign_target_id = ws.query_params.get("campaign_target_id")
    raw_org_id = ws.query_params.get("org_id")

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

    campaign_target_id = UUID(raw_campaign_target_id) if raw_campaign_target_id else None
    org_id = await _resolve_org_id(campaign_target_id, raw_org_id)
    log = logger.bind(caller=caller, call_uuid=call_uuid, provider=provider)
    log.info("voice_stream_connected")

    conversation_id: Any = None
    try:
        user_id, conversation_id = await voice_adapter.open_voice_conversation(
            caller, call_uuid, org_id=org_id
        )
        state = voice_adapter.CallState(
            user_id=user_id,
            conversation_id=conversation_id,
            org_id=org_id or UUID(settings.default_org_id),
            provider=provider,
            campaign_target_id=campaign_target_id,
            tts_provider=settings.voice_tts_provider,
        )
        if stream_id:
            state.stream_id = stream_id
        if campaign_target_id is not None:
            # Proof the call actually connected — tells the hangup webhook
            # (campaign_dialer.handle_call_ended) not to re-dial this person
            # just because qualify_lead didn't fire before they hung up.
            await voice_adapter.attach_campaign_conversation(campaign_target_id, conversation_id)
        instructions = await _system_instructions(campaign_target_id, org_id)

        # No OpenAI-Beta header on the GA endpoint — sending it alongside a GA
        # model name causes the connection to be rejected outright.
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        url = f"{_OPENAI_REALTIME_URL}?model={settings.openai_realtime_model}"

        async with websockets.connect(
            url, additional_headers=headers, max_size=None
        ) as oai:
            await oai.send(json.dumps(_session_update_event(instructions)))
            # Make the agent greet first instead of waiting for the caller.
            await oai.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {
                            "instructions": (
                                "Greet the caller warmly in one short, bilingual sentence "
                                "(e.g. a Hindi and English greeting together) so they can "
                                "reply in whichever language they're comfortable in - do not "
                                "ask them to choose a language."
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
                            break
                except WebSocketDisconnect:
                    log.info("call_ws_disconnected")
                except Exception as exc:  # noqa: BLE001
                    log.warning("pump_call_error", error=str(exc))
                finally:
                    await oai.close()

            async def pump_openai_to_call() -> None:
                try:
                    async for raw in oai:
                        event = json.loads(raw)
                        await voice_adapter.handle_openai_event(
                            event, ws, oai, state, log
                        )
                except websockets.ConnectionClosed:
                    log.info("openai_ws_closed")
                except Exception as exc:  # noqa: BLE001
                    log.warning("pump_openai_error", error=str(exc))

            call_started_at = datetime.now(UTC)
            tasks = [
                asyncio.create_task(pump_call_to_openai()),
                asyncio.create_task(pump_openai_to_call()),
            ]
            if call_uuid and state.org_id:
                tasks.append(
                    asyncio.create_task(
                        _watch_usage_limit(
                            state.org_id, call_started_at, oai, call_uuid, provider, log
                        )
                    )
                )
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("voice_stream_error", error=str(exc))
        await record_error()
    finally:
        if conversation_id is not None:
            await voice_adapter.close_voice_conversation(
                conversation_id, campaign_target_id=campaign_target_id
            )
        log.info("voice_stream_ended")
