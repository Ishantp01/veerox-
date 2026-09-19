"""Voice adapter — Realtime event handling + tool dispatch + persistence.

The voice channel does NOT call ``AgentCore.handle_turn`` (that's the text /
chat-completions path). Instead the OpenAI Realtime session runs the spoken
conversation directly, and this adapter is the thin translator around it:

  * converts the shared ``TOOL_DEFINITIONS`` into Realtime tool schemas,
  * dispatches Realtime function calls through the SAME ``DISPATCH_TABLE`` the
    WhatsApp/text path uses (the "one brain, two mouths" invariant),
  * resolves the caller to a ``User`` + open ``Conversation``, and
  * streams transcripts to Postgres *during* the call (the Realtime session is
    ephemeral — see longrunning/operations/pitfalls.md).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice import language_detect
from apps.api.config import settings
from apps.api.core.memory import persist_turn
from apps.api.core.tools import DISPATCH_TABLE, TOOL_DEFINITIONS
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.user import User
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)


@dataclass
class CallState:
    """Mutable per-call context shared between the two audio pump tasks."""

    user_id: UUID
    conversation_id: UUID
    org_id: UUID
    # "plivo" or "twilio" — which outbound WS message dialect to speak back
    # (see _send_media below). Set from the ?provider= query
    # param webhook.py put on the stream URL.
    provider: str = "plivo"
    stream_id: str | None = None
    pending_user_transcript: str | None = None
    # Set only for calls placed by the campaign dialer — lets qualify_lead
    # find the CampaignTarget row to update (see core/tools.py).
    campaign_target_id: UUID | None = None
    # "openai" (default, unchanged behavior) or "elevenlabs" — see
    # config.Settings.voice_tts_provider and elevenlabs_client.py. Fixed for
    # the lifetime of one call, copied from settings when the call starts.
    tts_provider: str = "openai"
    # Per-turn ElevenLabs streaming-TTS state, live only between the first
    # response.text.delta and that response finishing — None the rest of
    # the time. Not used when tts_provider is "openai".
    eleven_session: elevenlabs_client.ElevenLabsTTSSession | None = field(default=None, repr=False)
    eleven_forward_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    eleven_text_buffer: str = ""
    # The system instructions realtime_bridge.py sent in the initial
    # session.update — kept so the one-shot language-detection hint (see
    # _apply_language_hint below) can append to the real instructions
    # instead of overwriting them with a guessed placeholder.
    base_instructions: str = ""
    # Set once language_detect has run on the caller's first transcript of
    # the call, whether or not it resolved to anything — this fires at most
    # once per call, never on later turns (mid-call language switching stays
    # an explicit caller request, handled entirely by the model itself).
    language_hint_sent: bool = False
    # ``time.monotonic()`` deadline before which caller speech does NOT
    # barge in on the agent's response. Set by realtime_bridge.py when it
    # fires the opening greeting: on an outbound call the callee almost
    # always says "hello?" the instant they pick up, which would otherwise
    # trip ``input_audio_buffer.speech_started`` and wipe the greeting
    # before they hear any of it. Cleared to 0 once the greeting response
    # actually finishes, so normal barge-in resumes for the rest of the call.
    greeting_guard_until: float = 0.0
    greeting_guard_extended: bool = False
    # True between a response.created and its matching response.done — lets
    # the speech_started handler below tell whether the caller started
    # talking while the agent was actively generating/speaking a response.
    response_active: bool = False
    # Set when the caller talked over an in-progress response; consumed (and
    # cleared) by the input_audio_buffer.committed handler, which is what
    # actually fires the next response.create — create_response=False in
    # session.update means nothing else creates it automatically.
    interrupted_mid_response: bool = False
    # Caller finished speaking (input_audio_buffer.committed) while a response
    # was still playing — the reply is held until that response.done instead
    # of firing a second response.create on top of the active one.
    reply_pending: bool = False
    # The pending reply should be the "I heard you, you asked X" beat.
    restate_next: bool = False
    # The restate-only response is in flight; its response.done fires the
    # separate answer response so the two never run together.
    answer_after_restate: bool = False
    # Transcripts of everything the caller said while the agent was talking,
    # so the repeat-back beat can quote every question, not just the last.
    overlap_transcripts: list[str] = field(default_factory=list)
    # Mid-answer acknowledgment state machine. ~ACK_GAP_SECONDS after the
    # caller finishes a question that overlapped the agent's answer, the
    # answer is paused and the agent briefly says it heard them, then picks
    # its answer back up. None = idle; "cancelling" = response.cancel sent,
    # waiting for its response.done; "ack" = the short acknowledgment is
    # playing; "resume" = the interrupted answer is being continued.
    ack_stage: str | None = None
    ack_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    reply_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # ``time.monotonic()`` moment the audio already sent to the caller
    # finishes playing. The model generates audio far faster than real time,
    # so response.done arrives while seconds of speech are still queued at the
    # provider — "agent is speaking" must be judged from this, not from
    # response_active alone.
    playback_end: float = 0.0
    # input_audio_buffer.committed events whose transcript hasn't arrived
    # yet — the ack waits briefly for these so a filler word ("ok") can be
    # recognised and ignored before the agent reacts to it.
    transcripts_outstanding: int = 0
    # True from input_audio_buffer.speech_started to speech_stopped, so the
    # early-cut timer can tell whether the caller is still talking.
    caller_speaking: bool = False
    cut_task: "asyncio.Task[None] | None" = field(default=None, repr=False)


# Pause between the caller finishing their question and the agent cutting in
# to say it heard them.
ACK_GAP_SECONDS = 1.0
# How long to keep the agent talking while waiting for the caller's
# transcript, to tell a real question from filler ("ok", "theek hai").
TRANSCRIPT_WAIT_SECONDS = 4.0
# True: a real question asked while the agent is mid-answer cuts the current
# answer off and is answered straight away (no ack / read-back). False: the
# older behaviour — pause, "I heard you", resume, read-back, then answer.
ANSWER_IMMEDIATELY = True
# The agent stops talking once the caller has kept speaking this long over it
# — real questions run longer than a filler ("ok", "haan ji"), and it means
# the agent is already quiet by the time the caller finishes, so the answer
# needn't wait for the transcript.
EARLY_CUT_SECONDS = 0.8
# Silence between the read-back finishing and the answer starting.
ANSWER_GAP_SECONDS = 1.0
# mu-law at 8kHz, one byte per sample.
_PLAYBACK_BYTES_PER_SECOND = 8000


def _is_playing(state: CallState) -> bool:
    return state.playback_end > time.monotonic()


def _agent_busy(state: CallState) -> bool:
    return state.response_active or _is_playing(state)


_BACKCHANNEL_WORDS = frozenset(
    {
        "ok", "okay", "okk", "k", "kk", "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "uh", "um",
        "uhh", "umm", "oh", "ohh", "ah", "aah", "haan", "han", "haa", "ha", "hanji", "haanji",
        "ji", "achha", "accha", "acha", "achcha", "theek", "thik", "tik", "hai", "sahi",
        "right", "yes", "yeah", "yep", "yup", "sure", "fine", "alright", "all", "good", "nice",
        "cool", "great", "got", "it", "i", "see", "understood", "bilkul", "samajh", "gaya",
        "gayi", "sir", "madam", "mam", "ma'am", "अच्छा", "ठीक", "है", "हां", "हाँ", "हा",
        "जी", "हम्म", "ओके", "ओके।", "सही", "बिल्कुल", "समझ", "गया", "गई",
    }
)
_MAX_BACKCHANNEL_WORDS = 4


def _is_backchannel(text: str) -> bool:
    """True for empty/noise transcripts and short filler acknowledgments
    ("ok", "theek hai", "accha", "haan ji") — not real questions."""
    words = [w for w in (t.strip(".,!?;:।|\"'“”‘’()-…") for t in text.lower().split()) if w]
    if not words:
        return True
    return len(words) <= _MAX_BACKCHANNEL_WORDS and all(w in _BACKCHANNEL_WORDS for w in words)


async def _send_clear(ws: WebSocket, state: CallState) -> None:
    """Tell the provider to drop buffered playback audio."""
    state.playback_end = 0.0
    if state.provider == "twilio":
        message = {"event": "clear", "streamSid": state.stream_id}
    else:
        message = {"event": "clearAudio"}
    await ws.send_text(json.dumps(message))


async def _send_ack(oai_ws: Any, state: CallState) -> None:
    state.ack_stage = "ack"
    state.response_active = True
    await oai_ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "tool_choice": "none",
                    "instructions": (
                        f"{state.base_instructions}\n\n"
                        "IMPORTANT - the caller just asked something while you were "
                        "still talking, and you paused your answer. Say ONE short "
                        "sentence, in the language you've been using, telling them "
                        "you heard their question and will answer it right after "
                        "you finish your current point, e.g. \"aapka sawaal sun "
                        "liya, bas ye point khatam karke batata hoon.\" Do NOT "
                        "answer their question or add anything else."
                    ),
                },
            }
        )
    )


async def _ack_after_gap(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Wait ACK_GAP_SECONDS, then — if the agent is still mid-answer (still
    generating, or its audio is still playing) and the caller's question is
    waiting — pause the answer so the ack can play."""
    await asyncio.sleep(0 if ANSWER_IMMEDIATELY else ACK_GAP_SECONDS)
    # Give the transcript a moment to arrive: if it's just "ok"/"theek hai"
    # the pending reply is dropped and there's nothing to acknowledge.
    # The agent keeps talking while we wait — we only cut it off once we know
    # this is a real question.
    waited = 0.0
    while state.transcripts_outstanding > 0 and waited < TRANSCRIPT_WAIT_SECONDS:
        await asyncio.sleep(0.1)
        waited += 0.1
    if state.transcripts_outstanding > 0:
        # Transcript never showed up: don't cut the agent off on a guess (it
        # could be "ok"). The normal path answers after playback ends.
        state.transcripts_outstanding = 0
        return
    if not (_agent_busy(state) and state.reply_pending and state.ack_stage is None):
        return
    await _teardown_elevenlabs_turn(state)
    await _send_clear(call_ws, state)
    log.info("voice_ack_pausing_answer", still_generating=state.response_active)
    if ANSWER_IMMEDIATELY:
        # Real question mid-answer: drop the read-back beat, answer it now.
        state.restate_next = False
        state.interrupted_mid_response = False
        state.overlap_transcripts = []
        if state.response_active:
            state.ack_stage = "cancelling"
            await oai_ws.send(json.dumps({"type": "response.cancel"}))
        else:
            await _fire_pending_reply(oai_ws, state)
        return
    if state.response_active:
        # Wait for the cancelled response's response.done, then ack.
        state.ack_stage = "cancelling"
        await oai_ws.send(json.dumps({"type": "response.cancel"}))
    else:
        await _send_ack(oai_ws, state)


async def _cut_if_still_speaking(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Stop the agent talking once the caller has spoken over it for
    EARLY_CUT_SECONDS. The answer itself is sent by the normal reply path when
    their turn commits (the agent is no longer busy by then, so it fires at
    once with no transcript wait)."""
    await asyncio.sleep(EARLY_CUT_SECONDS)
    if not (state.caller_speaking and _agent_busy(state)):
        return
    if state.greeting_guard_until and time.monotonic() < state.greeting_guard_until:
        return
    log.info("voice_early_cut", still_generating=state.response_active)
    await _teardown_elevenlabs_turn(state)
    await _send_clear(call_ws, state)
    if state.response_active:
        await oai_ws.send(json.dumps({"type": "response.cancel"}))


async def _answer_after_readback(oai_ws: Any, state: CallState) -> None:
    """Answer the caller's overlapping questions after the read-back has
    finished playing plus ANSWER_GAP_SECONDS of silence, so they can tell
    where the answer starts."""
    while True:
        remaining = state.playback_end - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    await asyncio.sleep(ANSWER_GAP_SECONDS)
    # The caller spoke again (a newer reply is pending) or something else is
    # already responding — that path handles it.
    if state.reply_pending or state.response_active or state.ack_stage is not None:
        return
    state.response_active = True
    await oai_ws.send(
        json.dumps(
            {
                "type": "response.create",
                "response": {
                    "instructions": (
                        f"{state.base_instructions}\n\n"
                        "You just read back what the caller asked. Now answer ALL of "
                        "those questions, one after another, fully, in the language "
                        "you've been using. Begin with a short lead-in that makes it "
                        "clear the answer is starting, e.g. \"Ab aapke sawaal ka "
                        "jawab:\", and when there is more than one question, signal "
                        "each one (\"pehla sawaal...\", \"doosra sawaal...\")."
                    ),
                },
            }
        )
    )


async def _fire_reply_when_quiet(oai_ws: Any, state: CallState) -> None:
    """Hold the caller's pending reply until the audio already queued to them
    has finished playing, so the next answer starts after a real pause instead
    of stacking behind the previous one."""
    while True:
        remaining = state.playback_end - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    if state.reply_pending and state.ack_stage is None and not state.response_active:
        await _fire_pending_reply(oai_ws, state)


def _schedule_reply(oai_ws: Any, state: CallState) -> None:
    """Fire the pending reply now, or once queued audio has drained."""
    if _is_playing(state):
        if state.reply_task is None or state.reply_task.done():
            state.reply_task = asyncio.create_task(_fire_reply_when_quiet(oai_ws, state))
    else:
        asyncio.create_task(_fire_pending_reply(oai_ws, state))


async def _fire_pending_reply(oai_ws: Any, state: CallState) -> None:
    """Send the held-back reply for the caller's last turn.

    Normal turn: a plain response.create. If the caller talked over the
    previous answer, it is split in two beats: this one only says "I heard
    you" and repeats their question; the answer follows as its own response
    once this one's response.done arrives (see the handler).
    """
    state.reply_pending = False
    state.response_active = True
    if state.restate_next:
        state.restate_next = False
        state.answer_after_restate = True
        heard = "; ".join(f'"{t}"' for t in state.overlap_transcripts)
        hint = f" What they said (transcript): {heard}." if heard else ""
        await oai_ws.send(
            json.dumps(
                {
                    "type": "response.create",
                    "response": {
                        "tool_choice": "none",
                        "instructions": (
                            f"{state.base_instructions}\n\n"
                            "IMPORTANT - this reply is ONLY a read-back. The caller spoke "
                            "while you were still talking, and you have now finished your "
                            "earlier point. In the language you've been using, say you "
                            "heard them and tell them what they asked, e.g. \"aapne "
                            "poocha tha ki ... aur ... ok.\" Cover EVERY question or "
                            f"request they made while you were talking.{hint} Do NOT "
                            "answer, explain or give any information yet - end right after "
                            "repeating what they asked."
                        ),
                    },
                }
            )
        )
        state.overlap_transcripts = []
    else:
        await oai_ws.send(json.dumps({"type": "response.create"}))


def realtime_tools() -> list[dict[str, Any]]:
    """Convert chat-completions tool schemas to the flat Realtime format.

    Chat format nests under ``function``; the Realtime API wants
    ``name`` / ``description`` / ``parameters`` at the top level of each tool.
    """
    tools: list[dict[str, Any]] = []
    for definition in TOOL_DEFINITIONS:
        fn = definition.get("function", {})
        tools.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": fn.get("parameters"),
            }
        )
    return tools


def _normalize_phone(phone: str) -> str:
    """Strip everything but digits and a leading ``+`` (mirrors the other channels)."""
    return re.sub(r"[^\d+]", "", phone or "")


async def _get_or_create_user(db: AsyncSession, org_id: UUID, phone: str) -> User:
    stmt = select(User).where(User.org_id == org_id, User.phone == phone)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(org_id=org_id, phone=phone)
    db.add(user)
    await db.flush()
    return user


async def open_voice_conversation(
    caller: str, call_uuid: str | None = None, org_id: UUID | None = None
) -> tuple[UUID, UUID]:
    """Resolve the caller to a User and open a fresh voice Conversation.

    Owns its own session and commits immediately so transcripts persisted
    later (during the call) have a committed parent row to attach to.
    ``call_uuid`` is stored so the recording-finished webhook (which only
    reports CallUUID) can find its way back to this row.

    ``org_id`` comes from the answer_url's query string (see
    ``routers/admin.py``'s ``outbound_call``, which is the only caller that
    knows which dashboard org actually placed this call — a genuine inbound
    call, with no such context, falls back to the platform's default org).
    """
    org_id = org_id or UUID(settings.default_org_id)
    phone = _normalize_phone(caller)
    async with AsyncSessionLocal() as db:
        user = await _get_or_create_user(db, org_id, phone)
        conversation = Conversation(
            org_id=org_id, user_id=user.id, channel="voice", plivo_call_uuid=call_uuid or None
        )
        db.add(conversation)
        await db.commit()
        return user.id, conversation.id


async def attach_campaign_conversation(campaign_target_id: UUID, conversation_id: UUID) -> None:
    """Record that a campaign target's call actually connected.

    Called once the realtime bridge opens (``realtime_bridge.voice_stream``)
    — a target with ``conversation_id`` set is proof a real conversation
    happened, which is what tells the hangup webhook (``campaign_dialer.
    handle_call_ended``) NOT to re-dial this person just because the AI
    forgot to call ``qualify_lead`` before hanging up.
    """
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, campaign_target_id)
        if target is not None:
            target.conversation_id = conversation_id
            await db.commit()


async def close_voice_conversation(
    conversation_id: UUID, campaign_target_id: UUID | None = None
) -> None:
    """Mark the conversation ended when the call drops.

    If this was a campaign call and it ended without ``qualify_lead`` ever
    being invoked (no answer, hang-up mid-script, etc.), flip the target to
    ``failed`` so the dialer doesn't stall waiting on a target stuck
    ``calling`` forever. Always ``failed`` here, never re-queued to
    ``pending`` — by the time this runs the bridge connected (a real
    conversation happened), so retrying would re-call someone who already
    answered.
    """
    async with AsyncSessionLocal() as db:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None and conversation.ended_at is None:
            conversation.ended_at = datetime.now(UTC)

        if campaign_target_id is not None:
            target = await db.get(CampaignTarget, campaign_target_id)
            if target is not None and target.status == "calling":
                target.status = "failed"

        await db.commit()


async def save_call_recording(
    call_uuid: str, recording_url: str, duration_secs: float | None
) -> bool:
    """Attach a finished Plivo recording to its Conversation, matched by
    ``plivo_call_uuid``. Returns False if no matching conversation exists
    (e.g. the callback arrived for a call we didn't start recording for).
    """
    async with AsyncSessionLocal() as db:
        stmt = select(Conversation).where(Conversation.plivo_call_uuid == call_uuid)
        conversation = (await db.execute(stmt)).scalar_one_or_none()
        if conversation is None:
            return False
        conversation.recording_url = recording_url
        conversation.recording_duration_secs = duration_secs
        await db.commit()
        return True


async def _dispatch_realtime_tool(
    name: str,
    arguments_json: str,
    user_id: UUID,
    org_id: UUID,
    campaign_target_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> dict[str, Any]:
    """Run a Realtime function call through the shared ``DISPATCH_TABLE``.

    Mirrors ``apps.api.core.agent._dispatch_tool`` but owns a fresh DB session
    (the agent's request-scoped session doesn't exist on the voice path).
    ``org_id`` is the call's already-resolved tenant (``CallState.org_id``,
    see ``_resolve_org_id`` in realtime_bridge.py) — threaded through so tool
    handlers write leads/appointments/escalations to the org that actually
    owns the call, instead of falling back to the platform's default org.
    """
    handler = DISPATCH_TABLE.get(name)
    if handler is None:
        logger.warning("voice_tool_unknown", name=name)
        return {"status": "error", "reason": f"unknown_tool:{name}"}
    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return {"status": "error", "reason": "malformed_json_arguments"}
    if not isinstance(args, dict):
        return {"status": "error", "reason": "arguments_not_object"}
    async with AsyncSessionLocal() as db:
        result = await handler(
            db,
            user_id=user_id,
            org_id=org_id,
            channel="voice",
            campaign_target_id=campaign_target_id,
            conversation_id=conversation_id,
            **args,
        )
    return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}


async def _persist_voice_turn(state: CallState, assistant_text: str) -> None:
    """Persist one (user transcript, assistant transcript) pair mid-call."""
    user_text = state.pending_user_transcript or "(voice)"
    async with AsyncSessionLocal() as db:
        await persist_turn(
            db,
            conversation_id=state.conversation_id,
            user_id=state.user_id,
            org_id=state.org_id,
            channel="voice",
            user_text=user_text,
            assistant_text=assistant_text,
        )
    state.pending_user_transcript = None


async def _send_media(ws: WebSocket, state: CallState, payload: str) -> None:
    """Send one chunk of model audio back down the provider's WS.

    Plivo and Twilio Media Streams use different envelopes for the same
    mu-law payload — Plivo names the event ``playAudio`` and repeats the
    codec on every message; Twilio names it ``media`` and requires the
    ``streamSid`` it handed us in its own ``start`` event on every message
    instead.
    """
    if state.provider == "twilio":
        message = {
            "event": "media",
            "streamSid": state.stream_id,
            "media": {"payload": payload},
        }
    else:
        message = {
            "event": "playAudio",
            "media": {
                "contentType": "audio/x-mulaw",
                "sampleRate": 8000,
                "payload": payload,
            },
        }
    await ws.send_text(json.dumps(message))
    # base64 -> raw byte count; extend the playback horizon by that much audio.
    now = time.monotonic()
    state.playback_end = max(state.playback_end, now) + (len(payload) * 3 // 4) / (
        _PLAYBACK_BYTES_PER_SECOND
    )


async def _forward_elevenlabs_audio(call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Background task: drain one ElevenLabsTTSSession's audio queue onto
    the call as it arrives — started as soon as the first text delta opens
    the session, not only after the full response finishes, so playback
    starts as early as possible."""
    session = state.eleven_session
    if session is None:
        return
    try:
        async for chunk in session.audio_chunks():
            await _send_media(call_ws, state, chunk)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("elevenlabs_audio_forward_error", exc_info=True)


async def _teardown_elevenlabs_turn(state: CallState) -> None:
    """Tear down any in-flight ElevenLabs session/forward task — called both
    to cancel mid-speech on barge-in and to clean up after a turn finishes
    normally (the forward task is already done by then, so cancel() is a
    harmless no-op in that case)."""
    if state.eleven_forward_task is not None:
        state.eleven_forward_task.cancel()
        try:
            await state.eleven_forward_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        state.eleven_forward_task = None
    if state.eleven_session is not None:
        await state.eleven_session.__aexit__(None, None, None)
        state.eleven_session = None
    state.eleven_text_buffer = ""


async def _apply_language_hint(oai_ws: Any, state: CallState, text: str, log: Any) -> None:
    """One-shot: run language_detect on the caller's first transcript of the
    call and, if it resolves, nudge the session with the detected language
    instead of leaving the model to guess unaided. Runs as a background
    task (see its call site) so the LLM-fallback branch never stalls audio
    handling on the hot event-processing path."""
    language = await language_detect.detect_caller_language(text)
    if language is None:
        return
    updated_instructions = (
        f"{state.base_instructions}\n\n"
        f"Live language signal: the caller's own words indicate their language "
        f"is {language} - use it for your very next reply and the rest of the "
        f"call unless they explicitly ask you to switch."
    )
    try:
        await oai_ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {"type": "realtime", "instructions": updated_instructions},
                }
            )
        )
        log.info("voice_language_hint_applied", language=language)
    except Exception:  # noqa: BLE001
        log.warning("voice_language_hint_send_failed", exc_info=True)


async def handle_openai_event(
    event: dict[str, Any],
    call_ws: WebSocket,
    oai_ws: Any,
    state: CallState,
    log: Any,
) -> None:
    """Translate one OpenAI Realtime event into provider actions / tool calls."""
    etype = event.get("type")

    # GA renamed several beta event types (response.audio.delta ->
    # response.output_audio.delta, etc.) but docs are inconsistent on the
    # exact final names post-migration — accept both until confirmed live.
    if etype in ("response.audio.delta", "response.output_audio.delta"):
        delta = event.get("delta")
        if delta:
            await _send_media(call_ws, state, delta)

    elif etype == "input_audio_buffer.speech_started":
        # The caller started talking while the agent's current answer is
        # still playing (or before it's even started). This used to be
        # treated as a barge-in — clearing the provider's playback buffer
        # and cancelling the in-flight response — which cut the agent off
        # mid-answer. Now the current answer always finishes: OpenAI's own
        # turn_detection.interrupt_response=false (session.update above)
        # keeps generating/speaking it, and the caller's new speech becomes
        # the next turn once it does — see input_audio_buffer.committed
        # below, which is what actually fires that next response and (when
        # response_active is True right here) tells the model to acknowledge
        # the overlap first. greeting_guard_until is now redundant with the
        # interrupt_response setting but left in place as a harmless no-op
        # safety net.
        if state.greeting_guard_until and time.monotonic() < state.greeting_guard_until:
            log.info("voice_greeting_barge_in_suppressed")
            return
        state.caller_speaking = True
        if _agent_busy(state):
            state.interrupted_mid_response = True
            log.info("voice_caller_spoke_over_response")
            if ANSWER_IMMEDIATELY and (state.cut_task is None or state.cut_task.done()):
                state.cut_task = asyncio.create_task(
                    _cut_if_still_speaking(oai_ws, call_ws, state, log)
                )

    elif etype == "input_audio_buffer.speech_stopped":
        state.caller_speaking = False

    elif etype == "response.created":
        state.response_active = True

    elif etype == "input_audio_buffer.committed":
        # create_response=False (session.update above) means nothing else
        # triggers the next response — this is the one place we do, so it's
        # also the one place that can attach a one-off instruction to that
        # specific response without touching the persisted session
        # instructions used every other turn.
        state.transcripts_outstanding += 1
        if state.interrupted_mid_response:
            state.interrupted_mid_response = False
            state.restate_next = not ANSWER_IMMEDIATELY
        state.reply_pending = True
        # Still speaking the earlier answer: hold the reply, response.done
        # below fires it once that answer has fully finished.
        if not state.response_active:
            _schedule_reply(oai_ws, state)
        if (
            _agent_busy(state)
            and state.ack_stage is None
            and (state.ack_task is None or state.ack_task.done())
            and not (state.greeting_guard_until and time.monotonic() < state.greeting_guard_until)
        ):
            state.ack_task = asyncio.create_task(_ack_after_gap(oai_ws, call_ws, state, log))

    elif etype == "conversation.item.input_audio_transcription.completed":
        state.pending_user_transcript = (event.get("transcript") or "").strip()
        state.transcripts_outstanding = max(0, state.transcripts_outstanding - 1)
        overlapping = state.interrupted_mid_response or state.restate_next or state.reply_pending
        if overlapping and _is_backchannel(state.pending_user_transcript):
            # Filler while the agent was talking ("ok", "theek hai"): not a
            # question. If nothing real is waiting, drop the pending reply so
            # it triggers no ack, read-back or new answer.
            log.info("voice_backchannel_ignored", text=state.pending_user_transcript)
            if not state.overlap_transcripts and state.answer_after_restate is False:
                state.reply_pending = False
                state.restate_next = False
                state.interrupted_mid_response = False
        elif state.pending_user_transcript and overlapping:
            state.overlap_transcripts.append(state.pending_user_transcript)
        log.info("voice_user_transcript", text=state.pending_user_transcript)
        if not state.language_hint_sent:
            state.language_hint_sent = True
            if state.pending_user_transcript:
                asyncio.create_task(
                    _apply_language_hint(oai_ws, state, state.pending_user_transcript, log)
                )

    elif etype in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
        assistant_text = (event.get("transcript") or "").strip()
        if assistant_text:
            await _persist_voice_turn(state, assistant_text)
            log.info("voice_assistant_transcript", text=assistant_text)

    elif etype in ("response.text.delta", "response.output_text.delta"):
        # ElevenLabs path only — reached when session.update set
        # output_modalities=["text"], so OpenAI never emits audio deltas at
        # all. Opens the ElevenLabs session on the first delta and starts
        # forwarding its audio concurrently, rather than waiting for the
        # full response to finish.
        delta = event.get("delta") or ""
        if state.tts_provider == "elevenlabs" and delta:
            if state.eleven_session is None:
                state.eleven_session = await elevenlabs_client.ElevenLabsTTSSession().__aenter__()
                state.eleven_text_buffer = ""
                state.eleven_forward_task = asyncio.create_task(
                    _forward_elevenlabs_audio(call_ws, state, log)
                )
            state.eleven_text_buffer += delta
            await state.eleven_session.send_text(delta)

    elif etype in ("response.text.done", "response.output_text.done"):
        if state.tts_provider == "elevenlabs" and state.eleven_session is not None:
            await state.eleven_session.finish()
            if state.eleven_forward_task is not None:
                await state.eleven_forward_task
            assistant_text = (event.get("text") or state.eleven_text_buffer or "").strip()
            await _teardown_elevenlabs_turn(state)
            if assistant_text:
                await _persist_voice_turn(state, assistant_text)
                log.info("voice_assistant_transcript", text=assistant_text)

    elif etype == "response.function_call_arguments.done":
        call_id = event.get("call_id")
        name = event.get("name") or ""
        args_json = event.get("arguments") or "{}"
        result = await _dispatch_realtime_tool(
            name,
            args_json,
            state.user_id,
            state.org_id,
            campaign_target_id=state.campaign_target_id,
            conversation_id=state.conversation_id,
        )
        # Feed the tool result back into the session, then ask the model to
        # continue speaking with that result in context.
        await oai_ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result),
                    },
                }
            )
        )
        await oai_ws.send(json.dumps({"type": "response.create"}))
        log.info("voice_tool_dispatched", tool=name)

    elif etype == "response.done":
        state.response_active = False
        # The opening greeting has finished playing — lift the barge-in
        # suppression so normal interruption works for the rest of the call.
        # (its audio may still be queued at the provider, so hold the guard
        # until that playback ends.)
        if state.greeting_guard_until and not state.greeting_guard_extended:
            # Only the greeting's own response.done: extend the guard once, to
            # when the greeting audio stops. Later responses must not renew it
            # or every answer would suppress the caller's speech.
            state.greeting_guard_extended = True
            state.greeting_guard_until = state.playback_end
        else:
            state.greeting_guard_until = 0.0
        if state.ack_stage == "cancelling":
            if ANSWER_IMMEDIATELY:
                # The cut-off answer's response.done — answer the question now.
                state.ack_stage = None
                if state.reply_pending:
                    await _fire_pending_reply(oai_ws, state)
                return
            # The paused answer's response.done — play the short ack now.
            await _send_ack(oai_ws, state)
            return
        if state.ack_stage == "ack":
            state.ack_stage = "resume"
            state.response_active = True
            await oai_ws.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {
                            "tool_choice": "none",
                            "instructions": (
                                f"{state.base_instructions}\n\n"
                                "IMPORTANT - your previous answer was paused partway. "
                                "Pick it up from the point the caller last heard and finish "
                                "it, briefly, without repeating what was already said and "
                                "without answering their new question yet."
                            ),
                        },
                    }
                )
            )
            return
        if state.ack_stage == "resume":
            state.ack_stage = None
        if state.answer_after_restate:
            state.answer_after_restate = False
            # Restate beat is done. If the caller spoke again meanwhile, the
            # pending reply restates that newer question instead.
            if not state.reply_pending:
                asyncio.create_task(_answer_after_readback(oai_ws, state))
                return
        if state.reply_pending:
            _schedule_reply(oai_ws, state)

    elif etype == "error":
        log.warning("openai_realtime_error", error=event.get("error"))
        err = event.get("error") or {}
        if state.ack_stage == "cancelling" and "cancel_not_active" in str(err.get("code")):
            # The answer finished on its own just before our cancel landed;
            # no ack needed, carry on with the normal pending reply.
            state.ack_stage = None
            state.response_active = False
            if state.reply_pending:
                _schedule_reply(oai_ws, state)

    elif etype not in ("session.created", "session.updated"):
        log.info("voice_openai_event_unhandled", etype=etype)
