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
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import elevenlabs_client, instant_ack, sarvam_client
from apps.api.channels.voice.voice_profile import (
    LEGACY_VOICE_SETTINGS,
    ResolvedVoice,
    platform_model_id,
    platform_voice_id,
)
from apps.api.channels.voice import language_detect
from apps.api.channels.voice import call_control, recovery, turn_controller
from apps.api.channels.voice.latency import LatencyTracker
from apps.api.channels.voice.metering import CallUsage
from apps.api.config import settings
from apps.api.core import privacy
from apps.api.core.conversation import dialogue, runtime_stats, voice_decision
from apps.api.core.conversation.silence import SilenceMonitor
from apps.api.core.conversation.shadow import schedule_voice_shadow
from apps.api.core.memory import persist_turn
from apps.api.core.tools import DISPATCH_TABLE, TOOL_DEFINITIONS, get_or_create_lead_for_user
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.message import Message
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
    # This call's org voice profile merged over the platform settings
    # (voice_profile.resolve_profile), loaded per call by the bridge. None =
    # no profile: platform voice/model and the original handshake. Never a
    # process-global; one org's profile cannot reach another org's call.
    voice: "ResolvedVoice | None" = None
    # Per-turn ElevenLabs streaming-TTS state, live only between the first
    # response.text.delta and that response finishing — None the rest of
    # the time. Not used when tts_provider is "openai".
    eleven_session: elevenlabs_client.ElevenLabsTTSSession | None = field(default=None, repr=False)
    eleven_forward_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    eleven_text_buffer: str = ""
    # VOICE_ELEVENLABS_PREWARM: a session being opened in the background for
    # the response OpenAI just started, claimed by that response's first text
    # delta. Per-call and per-turn like eleven_session — never shared, never
    # reused for a second turn.
    eleven_prewarm_task: "asyncio.Task[elevenlabs_client.ElevenLabsTTSSession | None] | None" = (
        field(default=None, repr=False)
    )
    # VOICE_NONBLOCKING_TTS_FINISH: the task draining the last audio of a turn
    # whose text has finished. While it runs the agent still counts as playing
    # (see _is_playing), so nothing can start a second voice on top of it.
    eleven_finalize_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # Latency instrumentation (channels/voice/latency.py). Replaced by the
    # bridge with one carrying this call's ids; the default is anonymous.
    call_uuid: str | None = None
    latency: LatencyTracker = field(default_factory=LatencyTracker, repr=False)
    # The system instructions currently in force on the Realtime session —
    # starts as whatever realtime_bridge.py sent in the initial
    # session.update, and is kept in sync every time this adapter sends a
    # later session.update with new instructions (an explicit mid-call
    # language switch — see _apply_language_switch below). Every place that
    # rebuilds a one-off response.instructions string reads this field
    # rather than a connect-time snapshot, so it never regenerates a reply
    # from stale (e.g. pre-switch) instructions.
    base_instructions: str = ""
    # ``time.monotonic()`` deadline before which caller speech does NOT
    # barge in on the agent's response. Set by realtime_bridge.py when it
    # fires the opening greeting: on an outbound call the callee almost
    # always says "hello?" the instant they pick up, which would otherwise
    # trip ``input_audio_buffer.speech_started`` and wipe the greeting
    # before they hear any of it. Cleared to 0 once the greeting response
    # actually finishes, so normal barge-in resumes for the rest of the call.
    greeting_guard_until: float = 0.0
    t_speech_started: float = 0.0
    backchannel_marked: bool = False  # non_interruptive_backchannel already recorded for this overlap
    ack_clip_index: int = 0  # rotates the instant-reaction clips
    greeting_guard_extended: bool = False
    # True between a response.created and its matching response.done — lets
    # the speech_started handler below tell whether the caller started
    # talking while the agent was actively generating/speaking a response.
    response_active: bool = False
    reconnecting: bool = False
    tool_response_pending: bool = False
    tool_goodbye_pending: bool = False
    completed_response_ids: set[str] = field(default_factory=set)
    seen_tool_call_ids: set[str] = field(default_factory=set)
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
    # time.monotonic() of the latest input_audio_buffer.committed (0 = none yet); trace events only.
    t_committed: float = 0.0
    # Live decision for the caller's latest turn (core/conversation/
    # voice_decision.py). Computed in the background from the final transcript
    # and consumed by _fire_pending_reply if it is ready in time; None when
    # the org isn't on live voice mode. voice_actions_done makes sure the same
    # action never runs twice in one call.
    voice_decision_task: "asyncio.Task[Any] | None" = field(default=None, repr=False)
    voice_decision_text: str = ""
    voice_decision_started: float = 0.0
    voice_actions_done: set[str] = field(default_factory=set)
    # Silence handling (core/conversation/silence.py): only created for orgs on
    # live voice mode, the first time one of the agent's responses finishes.
    silence: SilenceMonitor | None = field(default=None, repr=False)
    silence_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # Mid-answer acknowledgment state machine. ~ACK_GAP_SECONDS after the
    # caller finishes a question that overlapped the agent's answer, the
    # answer is paused and the agent briefly says it heard them, then picks
    # its answer back up. None = idle; "cancelling" = response.cancel sent,
    # waiting for its response.done; "ack" = the short acknowledgment is
    # playing; "resume" = the interrupted answer is being continued.
    ack_stage: str | None = None
    # True while a response is being muted-not-cancelled (see _cut_now):
    # generation keeps running server-side so we don't risk killing the
    # session, but its audio/text deltas must not reach the caller. Cleared
    # on that response's response.done, right where ack_stage=="cancelling"
    # is otherwise consumed.
    suppress_response_audio: bool = False
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
    # Live partial-transcript path (turn_controller.LiveTranscriber). ``live``
    # is None until (and unless) its connection comes up.
    live: Any = field(default=None, repr=False)
    oai_ws: Any = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    # Text streamed for the caller's current utterance (reset on speech_started).
    utterance_text: str = ""
    # monotonic deadline until which live text may still trigger a cut of the
    # agent's speech; 0 = not judging.
    cut_eval_deadline: float = 0.0
    live_llm_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    live_llm_calls: int = 0
    live_llm_last_words: int = 0
    live_llm_said_filler: bool = False
    # Counters for the live turn-detection path (numbers only; logged once at the end of the call).
    live_stats: turn_controller.LiveTurnStats = field(default_factory=turn_controller.LiveTurnStats)
    # Adaptive endpointing: don't answer before this monotonic time (0 = no hold).
    # Set when the caller's turn commits but the streamed text looks unfinished
    # ("haan, mera matlab..."); cleared as soon as they resume or the text
    # stops looking unfinished.
    hold_until: float = 0.0
    # Latency instrumentation: when the caller's speech ended / when the reply
    # request went out (monotonic; 0 = not measuring). Logged once the first
    # audio or text of that reply arrives (voice_reply_latency).
    t_speech_stopped: float = 0.0
    t_reply_sent: float = 0.0
    # finish_first mode: a real question was recognised while the caller was
    # still speaking; the pause-and-acknowledge starts once their turn ends.
    finish_first_pending: bool = False
    # Text and audio length of the response currently playing / last played, so
    # a paused answer can be resumed from roughly where the caller stopped
    # hearing it (finish_first mode).
    resp_text: str = ""
    resp_audio_seconds: float = 0.0
    paused_text: str = ""
    paused_heard_pct: int = 0
    hold_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # The caller's last words asked for time ("ek minute"): be patient with the silence that follows.
    caller_thinking: bool = False
    cut_at: float = 0.0
    # Tools the enhanced dialogue says the model may not run on this turn ({tool: reason}); see
    # dialogue.tool_holds. Reset on every caller transcript, so a stale hold can never block a later turn.
    tool_holds: dict[str, str] = field(default_factory=dict)
    # Seen on this call: the org is on the enhanced dialogue / the caller refused a meeting. They let the holds
    # still apply on a turn whose decision plan is late.
    dialogue_enhanced: bool = False
    meeting_declined: bool = False
    # The most recent FULLY COMPLETED thing the agent said (set on every
    # response.audio_transcript.done). Unlike resp_text (reset to "" at the
    # start of every response, so often empty when an ack/restate beat needs
    # a language sample) or paused_text (frozen once by a finish_first pause
    # and, before this field existed, never refreshed afterwards — see
    # _language_sample), this always reflects the call's actual current
    # language, however long ago the last full turn finished.
    last_spoken_text: str = ""
    # --- Phase 3 interruption state (VOICE_SAFE_CANCEL / VOICE_SMART_TURN_TAKING) ---
    # Id of the response OpenAI is currently generating (from response.created).
    response_id: str | None = None
    # Responses the caller cut off. Every late event they emit (audio, text,
    # transcript, tool call) is dropped by id, so a cancelled answer can never be
    # heard or acted on. Bounded; per call.
    cancelled_response_ids: set[str] = field(default_factory=set)
    # Bumped on every cut/reconnect; long-lived audio forwarders compare it to the
    # value they started with and stop forwarding the moment it changes.
    generation: int = 0
    # Circuit breaker: after a cancel misbehaves this call goes back to muting
    # (never cancelling) for the rest of the call.
    cancel_disabled: bool = False
    cancel_failures: int = 0
    # time.monotonic() when the last response.cancel was sent; 0 = none outstanding.
    cancel_sent_at: float = 0.0
    cancel_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # The caller transcript the in-flight normal reply is answering, so an
    # interrupted answer can still be recorded with the question it was for.
    answering_transcript: str | None = None
    # The next normal reply answers an interruption: tell the model to address it first.
    priority_reply: bool = False
    # --- Phase 5: ending calls, TTS recovery, reconnect safety, metering ---
    # The provider's media-stream socket, so code outside the pumps can close it.
    call_ws: Any = field(default=None, repr=False)
    call_ended: bool = False  # the provider/caller side is gone
    hangup_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    hangup_done: bool = False
    hangup_committed: bool = False
    hangup_abortable: bool = True
    hangup_reason: str | None = None
    responses_done: int = 0
    responses_done_at_request: int = 0
    tts_failures: int = 0
    tts_turn_retried: bool = False
    tts_failed_over: bool = False
    tts_text_done: bool = False
    tts_final_text: str = ""
    tts_final_pending: str | None = None
    eleven_turn_audio_chunks: int = 0
    tts_watchdog_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    tts_recovery_task: "asyncio.Task[None] | None" = field(default=None, repr=False)
    # State-changing tool results this call, so a repeat (a model retry, or a replay after a
    # reconnect) returns the earlier result instead of booking / messaging twice.
    action_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_actions: list[str] = field(default_factory=list)
    usage: CallUsage = field(default_factory=CallUsage, repr=False)
    consent_tool: bool = False  # the org asks for recording consent on this call
    recording_started: bool = False
    tts_last_text_at: float = 0.0  # when text last went to the TTS socket (the watchdog measures idle time from it)
    # Interrupted-answer trimming (VOICE_TRIM_INTERRUPTED_CONTEXT)
    response_items: dict[str, list[str]] = field(default_factory=dict)
    item_previous: dict[str, str | None] = field(default_factory=dict)
    heard_snapshot: str | None = None
    trim_pending: dict[str, str] = field(default_factory=dict)


# Pause between the caller finishing their question and the agent cutting in
# to say it heard them.
ACK_GAP_SECONDS = 1.0
# How long to keep the agent talking while waiting for the caller's
# transcript, to tell a real question from filler ("ok", "theek hai").
TRANSCRIPT_WAIT_SECONDS = 4.0
def _answer_immediately() -> bool:
    """True (default, VOICE_INTERRUPTION_MODE=answer_now): a real question asked
    while the agent is mid-answer cuts the answer off and is answered straight
    away. False (finish_first): pause, say "I heard you", resume and finish the
    point, read the question back, then answer it."""
    return settings.voice_interruption_mode != "finish_first"
# The agent stops talking once the caller has kept speaking this long over it
# — real questions run longer than a filler ("ok", "haan ji"), and it means
# the agent is already quiet by the time the caller finishes, so the answer
# needn't wait for the transcript.
# 1.5s, not 0.8s: "hmm, theek hai" takes about a second to say, so a shorter window cut the agent off for
# plain acknowledgements whenever there was no transcript to tell them apart.
EARLY_CUT_SECONDS = 1.5
# With the live transcriber up, the agent is cut off by what the caller SAYS
# (see handle_live_delta), not by how long they talk. If no verdict has
# formed after this long of overlapping speech, cut anyway unless the text
# so far is known to be filler. NOT purely a wait-for-the-LLM budget — the
# live transcript itself lags real speech by up to ~1s (see
# LIVE_EVAL_TAIL_SECONDS below), so pushing this below that lag (0.5s was
# tried and reverted) makes the fallback fire on an empty/partial fragment
# before "ok"/"achha" has even fully arrived as text, misreading it as a
# real turn and cutting on plain fillers. 0.8s is the floor found safe in
# practice — below it, speed comes at the cost of exactly the false-cutoff
# problem this classifier exists to prevent.
LIVE_FALLBACK_CUT_SECONDS = 0.8
# If NO text has arrived at all by the fallback time, the caller is probably mid-"hmm thik hai" and the transcript is
# simply late: keep waiting for text (re-checking) up to this long after they started, then cut on duration alone.
LIVE_NO_TEXT_CUT_SECONDS = 2.0
# Live text lags speech by up to ~1s, so keep judging for this long after
# the caller stops speaking.
LIVE_EVAL_TAIL_SECONDS = 3.0
# At most this many LLM filler/real checks per caller utterance.
LIVE_LLM_MAX_CALLS = 3
# Silence between the read-back finishing and the answer starting.
ANSWER_GAP_SECONDS = 1.0
# mu-law at 8kHz, one byte per sample.
_PLAYBACK_BYTES_PER_SECOND = 8000


def _finalizing(state: CallState) -> bool:
    """True while a turn's last ElevenLabs audio is still being drained."""
    task = state.eleven_finalize_task
    return task is not None and not task.done()


def _is_playing(state: CallState) -> bool:
    # Audio still being synthesized counts as playing: playback_end only
    # covers what has already been handed to the provider.
    return state.playback_end > time.monotonic() or _finalizing(state)


async def _wait_until_quiet(state: CallState) -> None:
    """Return once nothing is left to hear: the turn's audio has been fully
    drained from ElevenLabs AND the audio already queued at the provider has
    played. Loops because either can extend the other."""
    while True:
        task = state.eleven_finalize_task
        if task is not None and not task.done():
            await asyncio.wait({task})  # doesn't raise if the task is cancelled/failed
            continue
        remaining = state.playback_end - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)


def _agent_busy(state: CallState) -> bool:
    return state.response_active or _is_playing(state)


# The filler-word list and classifier live in turn_controller.py; kept under
# the old name because the whisper-transcript path below still uses it.
_is_backchannel = turn_controller.is_extended_backchannel


async def _send_clear(ws: WebSocket, state: CallState) -> None:
    """Tell the provider to drop buffered playback audio."""
    state.playback_end = 0.0
    if state.provider == "twilio":
        message = {"event": "clear", "streamSid": state.stream_id}
    else:
        message = {"event": "clearAudio"}
    await ws.send_text(json.dumps(message))
    state.latency.interruption_cleared()


# The pause / acknowledge / resume / read-back lines are sent as ISOLATED
# responses: ``conversation: "none"`` plus a hand-built ``input``. A normal
# response.create sees the caller's still-unanswered question in the
# conversation, and the model then simply answers it (in production it answered
# the same question four times, ignoring "say ONE short sentence"). An isolated
# response only sees what we hand it, so it cannot.
_OOB_PERSONA = (
    "You are the AI voice agent on a live phone call. Speak briefly and naturally, in "
    "plain spoken sentences: no markdown, no lists. Do exactly what the request says "
    "and nothing more."
)


def _oob_response(request: str, context: str) -> str:
    return json.dumps(
        {
            "type": "response.create",
            "response": {
                "conversation": "none",
                "tool_choice": "none",
                "instructions": f"{_OOB_PERSONA}\n\n{request}",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": context}],
                    }
                ],
            },
        }
    )


def _language_sample(state: CallState) -> str:
    """A snippet of what the agent has been saying, so an isolated response
    speaks the call's language even though it can't see the conversation.
    Freshest signal first: the in-progress response's text so far, then the
    text frozen by an active finish_first pause (state.paused_text — only
    meaningful between that pause and its own resume finishing, see the
    response.done handler which clears it once consumed), then the last
    fully-completed turn as the fallback for e.g. a beat firing right at the
    very start of a new response before any transcript delta has arrived."""
    return (
        state.resp_text or state.paused_text or state.last_spoken_text or ""
    ).strip()[:200]


async def _send_ack(oai_ws: Any, state: CallState) -> None:
    state.ack_stage = "ack"
    state.response_active = True
    await oai_ws.send(
        _oob_response(
            "The caller just asked you something while you were still talking, so you "
            "paused. Say ONE short sentence telling them you heard their question and "
            "will answer it right after you finish your current point, for example "
            "\"aapka sawaal sun liya, bas ye point khatam karke batata hoon.\" Say it "
            "in the same language as the sample below. Do NOT answer their question, do "
            "not ask anything, add nothing else.",
            f"Language sample of the call so far: {_language_sample(state)!r}",
        )
    )


async def _send_resume(oai_ws: Any, state: CallState) -> None:
    """Finish the paused answer: hand the model the text it was saying and how
    much of it the caller already heard."""
    state.ack_stage = "resume"
    state.response_active = True
    await oai_ws.send(
        _oob_response(
            "You paused partway through the answer below to acknowledge the caller. "
            "Finish it now: say only the part they have NOT heard yet, in at most two "
            "short spoken sentences, continuing naturally (no greeting, no repeating what "
            "they already heard, no mention of the pause). Do NOT answer any other "
            "question.",
            f"The full answer you were giving:\n{state.paused_text}\n\n"
            f"The caller has already heard roughly the first {state.paused_heard_pct}% of it.",
        )
    )


async def _ack_after_gap(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Wait ACK_GAP_SECONDS, then — if the agent is still mid-answer (still
    generating, or its audio is still playing) and the caller's question is
    waiting — pause the answer so the ack can play."""
    await asyncio.sleep(0 if _answer_immediately() else ACK_GAP_SECONDS)
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
    _snapshot_heard(state)
    await _teardown_elevenlabs_turn(state)
    await _send_clear(call_ws, state)
    log.info("voice_ack_pausing_answer", still_generating=state.response_active)
    if _answer_immediately():
        # Real question mid-answer: drop the read-back beat, answer it now.
        state.restate_next = False
        state.interrupted_mid_response = False
        state.overlap_transcripts = []
        if state.response_active:
            # No response.cancel while generating — see _cut_now's docstring:
            # this exact action was found to make OpenAI silently close the
            # whole Realtime session a couple of seconds later. Mute locally
            # (already done above) and let it finish naturally instead.
            # (VOICE_SAFE_CANCEL changes this: see _stop_generation.)
            if settings.voice_smart_turn_taking:
                state.priority_reply = True
            await _stop_generation(oai_ws, state, log)
        else:
            await _fire_pending_reply(oai_ws, state)
        return
    if state.response_active:
        # Wait for the muted response's own response.done, then ack.
        state.ack_stage = "cancelling"
        state.suppress_response_audio = True
    else:
        await _send_ack(oai_ws, state)


def _note_first_output(state: CallState, log: Any, kind: str) -> None:
    """Log how long the caller waited: from the end of their speech, and from the
    moment the reply was requested, to the first audio/text of the answer."""
    if not state.t_reply_sent:
        return
    now = time.monotonic()
    log.info(
        "voice_reply_latency",
        kind=kind,
        since_speech_stopped_ms=int((now - state.t_speech_stopped) * 1000)
        if state.t_speech_stopped
        else None,
        since_reply_sent_ms=int((now - state.t_reply_sent) * 1000),
    )
    state.t_reply_sent = 0.0


def _live_ready(state: CallState) -> bool:
    return state.live is not None and state.live.healthy


async def _start_finish_first(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """finish_first mode: pause the agent's answer so it can say it heard the
    caller's question. The rest (short ack, resume, read-back, answer) runs from
    the response.done handler via ``ack_stage``."""
    if not _agent_busy(state) or state.ack_stage is not None:
        return
    if state.greeting_guard_until and time.monotonic() < state.greeting_guard_until:
        return
    state.finish_first_pending = False
    state.cut_eval_deadline = 0.0
    state.restate_next = True  # read the question back after the point is finished
    state.reply_pending = True
    log.info("voice_finish_first_pause", still_generating=state.response_active)
    # Remember what was being said and how much of it was heard, before the
    # queued audio is cleared, so the point can be finished afterwards.
    state.paused_text = state.resp_text.strip()
    if state.resp_audio_seconds > 0:
        unheard = max(0.0, state.playback_end - time.monotonic())
        heard = max(0.0, state.resp_audio_seconds - unheard)
        state.paused_heard_pct = max(0, min(95, int(100 * heard / state.resp_audio_seconds)))
    else:
        state.paused_heard_pct = 0
    await _teardown_elevenlabs_turn(state)
    await _send_clear(call_ws, state)
    if state.response_active:
        # No response.cancel while generating — see _cut_now's docstring.
        # paused_text/paused_heard_pct above already captured what to resume
        # later; the muted response finishing quietly in the background
        # doesn't affect that.
        state.ack_stage = "cancelling"
        state.suppress_response_audio = True
    else:
        await _send_ack(oai_ws, state)


async def _on_real_turn(
    oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any, reason: str
) -> None:
    """The live text says the caller asked something real while the agent talks."""
    if _answer_immediately():
        await _cut_now(oai_ws, call_ws, state, log, reason)
        return
    if state.caller_speaking:
        # Let them finish asking; the pause + "I heard you" starts when their
        # turn commits so the two voices don't overlap.
        state.finish_first_pending = True
        log.info("voice_finish_first_pending", reason=reason)
    else:
        await _start_finish_first(oai_ws, call_ws, state, log)


# A cancel that gets no response.done within settings.voice_cancel_timeout_seconds is
# treated as failed (the breaker trips, the old mute-and-wait carries on); if the
# response is STILL not done after this long the response is written off so the
# call cannot wait forever.
CANCEL_HARD_TIMEOUT_SECONDS = 10.0
# A session close this soon after a cancel is attributed to it.
CANCEL_CLOSE_WINDOW_SECONDS = 10.0
_MAX_TRACKED_CANCELLED = 64

INTERRUPT_PRIORITY_INSTRUCTION = (
    "The caller just interrupted you. Do NOT continue or repeat what you were saying. First "
    "answer what they just said or asked, directly and briefly, in the same language they used. "
    "Use only facts stated in your instructions or in approved information you were given; never "
    "invent or estimate profits, returns, income, prices, timelines or guarantees. If you do not "
    "have an approved figure, say honestly that the team will confirm it. Only after answering, "
    "and only if it fits naturally, return to the next step of your plan with one short question; "
    "never re-ask something they have already answered."
)

# Response events whose payload belongs to ONE response; dropped when that response was cancelled.
_RESPONSE_CONTENT_EVENTS = frozenset(
    {
        "response.audio.delta",
        "response.output_audio.delta",
        "response.audio_transcript.delta",
        "response.output_audio_transcript.delta",
        "response.audio_transcript.done",
        "response.output_audio_transcript.done",
        "response.text.delta",
        "response.output_text.delta",
        "response.text.done",
        "response.output_text.done",
        "response.function_call_arguments.done",
    }
)


def _is_stale_response(state: CallState, event: dict[str, Any]) -> bool:
    rid = event.get("response_id")
    return bool(rid) and rid in state.cancelled_response_ids


def _trip_cancel_breaker(state: CallState, log: Any, reason: str) -> None:
    """Stop sending response.cancel for the rest of this call."""
    state.cancel_failures += 1
    if not state.cancel_disabled:
        state.cancel_disabled = True
        log.warning("voice_cancel_disabled", reason=reason, failures=state.cancel_failures)


def _clear_cancel_outstanding(state: CallState) -> None:
    state.cancel_sent_at = 0.0
    task, state.cancel_task = state.cancel_task, None
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


def note_openai_closed(state: CallState, log: Any) -> None:
    """The Realtime socket closed. If we had just sent a cancel, that is the
    suspected failure mode: never cancel again on this call."""
    if state.cancel_sent_at and time.monotonic() - state.cancel_sent_at < CANCEL_CLOSE_WINDOW_SECONDS:
        log.warning("voice_cancel_suspected_session_close")
        _trip_cancel_breaker(state, log, "session_closed_after_cancel")


def reset_after_reconnect(state: CallState) -> None:
    """A fresh Realtime session has no memory of the old responses: drop every
    id and in-flight marker so nothing refers to one that no longer exists."""
    _clear_cancel_outstanding(state)
    state.response_id = None
    state.tool_response_pending = False
    state.tool_goodbye_pending = False
    state.completed_response_ids.clear()
    state.seen_tool_call_ids.clear()
    state.cancelled_response_ids.clear()
    state.generation += 1
    state.priority_reply = False
    state.answering_transcript = None
    state.response_items.clear()
    state.item_previous.clear()
    state.trim_pending.clear()
    state.tool_holds.clear()
    state.dialogue_enhanced = False
    state.meeting_declined = False
    state.heard_snapshot = None


async def _persist_quietly(state: CallState, text: str, pending: str | None, log: Any) -> None:
    try:
        await _persist_voice_turn(state, text, pending=pending)
    except Exception:  # noqa: BLE001 - recording must never touch the call
        log.warning("voice_interrupted_turn_persist_failed", exc_info=True)


def _persist_interrupted(state: CallState, log: Any) -> None:
    """The answer to the caller's last question was cut off: record the part that
    was generated against that question. Otherwise the muted response is never
    persisted and the caller's turn silently vanishes from the history."""
    pending, state.answering_transcript = state.answering_transcript, None
    text = state.resp_text.strip()
    if pending is None or not text:
        return
    asyncio.create_task(_persist_quietly(state, text, pending, log))


async def _cancel_watchdog(oai_ws: Any, state: CallState, log: Any) -> None:
    """A cancel must be confirmed by response.done. If it is not, fall back to
    the old behaviour instead of trusting it (soft), and write the response off
    rather than wait forever (hard)."""
    try:
        await asyncio.sleep(settings.voice_cancel_timeout_seconds)
        if state.cancel_sent_at and state.ack_stage == "cancelling":
            _trip_cancel_breaker(state, log, "no_response_done_after_cancel")
        await asyncio.sleep(max(0.0, CANCEL_HARD_TIMEOUT_SECONDS - settings.voice_cancel_timeout_seconds))
        if state.cancel_sent_at and state.ack_stage == "cancelling" and state.response_active:
            log.warning("voice_cancel_hard_timeout")
            if state.response_id:
                # A late completion of the written-off response must not
                # release the slot reserved for the replacement answer.
                if len(state.completed_response_ids) >= 256:
                    state.completed_response_ids.pop()
                state.completed_response_ids.add(state.response_id)
            state.cancel_sent_at = 0.0
            state.response_active = False
            state.suppress_response_audio = False
            state.ack_stage = None
            if state.reply_pending:
                _schedule_reply(oai_ws, state)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("voice_cancel_watchdog_failed", exc_info=True)


def _snapshot_heard(state: CallState) -> None:
    """Before an interruption clears playback, work out roughly how much of the current answer the caller
    actually heard: the share of its audio that had already played, applied to its words. An estimate (the
    provider reports no playback position); it errs toward dropping words rather than claiming unheard
    ones were said. Only when VOICE_TRIM_INTERRUPTED_CONTEXT is on."""
    if not settings.voice_trim_interrupted_context:
        return
    words = state.resp_text.split()
    if not words or state.resp_audio_seconds <= 0:
        state.heard_snapshot = ""
        return
    unheard = max(0.0, state.playback_end - time.monotonic())
    fraction = max(0.0, min(1.0, (state.resp_audio_seconds - unheard) / state.resp_audio_seconds))
    state.heard_snapshot = " ".join(words[: int(len(words) * fraction)])


async def _trim_context(oai_ws: Any, state: CallState, log: Any, response_id: str) -> None:
    """The interrupted response has finished: remove its items from the Realtime conversation, and put back
    only the part the caller heard (placed where the original was, so order is kept). Without this the model
    believes it said all of it, and skips or contradicts what the caller never got to hear.

    UNVERIFIED against a live Realtime session (item delete/insert on a text-only response); off by default.
    Never raises."""
    heard = state.trim_pending.pop(response_id, "")
    items = state.response_items.pop(response_id, [])
    if not items:
        return
    try:
        previous = state.item_previous.get(items[0])
        for item_id in items:
            await oai_ws.send(json.dumps({"type": "conversation.item.delete", "item_id": item_id}))
        if heard and previous:
            await oai_ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "previous_item_id": previous,
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": heard}],
                        },
                    }
                )
            )
        log.info("voice_interrupted_context_trimmed", heard_words=len(heard.split()), items=len(items))
    except Exception:  # noqa: BLE001
        log.warning("voice_context_trim_failed", exc_info=True)


async def _stop_generation(oai_ws: Any, state: CallState, log: Any) -> None:
    """The caller is being answered instead of the response still generating.

    Always: mute it locally and wait for its response.done (the original,
    proven behaviour). With VOICE_SAFE_CANCEL (and the breaker not tripped) it
    is also cancelled upstream, so it stops generating and response.done
    arrives at once instead of when the old answer would have finished. Late
    events of a cancelled response are discarded by id (_is_stale_response)."""
    state.ack_stage = "cancelling"
    state.suppress_response_audio = True
    state.generation += 1
    if settings.voice_trim_interrupted_context and state.response_id:
        state.trim_pending[state.response_id] = state.heard_snapshot or ""
    if not settings.voice_safe_cancel:
        return
    _persist_interrupted(state, log)
    if state.cancel_disabled:
        return
    rid = state.response_id
    if rid is not None and rid in state.cancelled_response_ids:
        return  # never cancel the same response twice
    payload: dict[str, Any] = {"type": "response.cancel"}
    if rid is not None:
        payload["response_id"] = rid
        state.cancelled_response_ids.add(rid)
        while len(state.cancelled_response_ids) > _MAX_TRACKED_CANCELLED:
            state.cancelled_response_ids.pop()
    state.cancel_sent_at = time.monotonic()
    try:
        await oai_ws.send(json.dumps(payload))
    except Exception as exc:  # noqa: BLE001
        _trip_cancel_breaker(state, log, f"send_failed:{type(exc).__name__}")
        return
    log.info("voice_response_cancel_sent", response_id=rid)
    if state.cancel_task is None or state.cancel_task.done():
        state.cancel_task = asyncio.create_task(_cancel_watchdog(oai_ws, state, log))


def _with_priority(response: dict[str, Any] | None, state: CallState) -> dict[str, Any]:
    """Add the answer-the-interruption-first instruction to a response.create body,
    keeping whatever instructions (e.g. the decision layer's) it already carried."""
    out = dict(response or {})
    base = out.get("instructions") or state.base_instructions
    out["instructions"] = f"{base}\n\n{INTERRUPT_PRIORITY_INSTRUCTION}".strip()
    return out


async def _cut_now(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any, reason: str) -> None:
    """Stop the agent mid-speech: drop queued audio and make sure the
    caller's turn gets answered.

    Cancellation follows VOICE_SAFE_CANCEL and its per-call breaker/watchdog;
    otherwise the response is muted locally until response.done.
    """
    if not _agent_busy(state):
        return
    if state.greeting_guard_until and time.monotonic() < state.greeting_guard_until:
        return
    state.cut_eval_deadline = 0.0
    state.live_stats.count_cut(reason)
    _snapshot_heard(state)
    state.latency.interruption_cut_decided()
    if settings.voice_smart_turn_taking:
        state.priority_reply = True  # the answer to this interruption comes first
    state.cut_at = time.monotonic()
    log.info(
        "voice_agent_cut", reason=reason, still_generating=state.response_active,
        caller_speaking=state.caller_speaking, transcript_chars=len(state.utterance_text),
        playback_remaining_ms=round(max(0.0, state.playback_end - time.monotonic()) * 1000),
        live_filler=state.live_llm_said_filler,
    )
    # Suppress deltas before teardown yields to an in-flight TTS send.
    state.suppress_response_audio = True
    await _teardown_elevenlabs_turn(state)
    await _send_clear(call_ws, state)
    if not state.caller_speaking:
        # Their turn already ended (its reply may have been dropped as filler
        # earlier); make sure it is answered now.
        state.reply_pending = True
    if state.response_active:
        # response.done (whenever the muted response naturally finishes)
        # fires the pending reply — see the ack_stage=="cancelling" branch
        # in handle_openai_event's response.done handler. suppress_response_audio
        # stops its still-arriving deltas reaching the caller in the meantime.
        await _stop_generation(oai_ws, state, log)
    elif state.reply_pending:
        _schedule_reply(oai_ws, state)


async def _cut_if_still_speaking(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Duration-based fallback (no live transcript): stop the agent once the
    caller has spoken over it for EARLY_CUT_SECONDS. The answer itself is sent
    by the normal reply path when their turn commits (the agent is no longer
    busy by then, so it fires at once with no transcript wait)."""
    await asyncio.sleep(EARLY_CUT_SECONDS)
    if not state.caller_speaking:
        return
    await _cut_now(oai_ws, call_ws, state, log, "duration")


def _live_text_stalled(state: CallState) -> bool:
    """The live transcriber is gone, so waiting for text is pointless."""
    return not _live_ready(state)


async def _live_fallback_cut(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Live path safety net: the caller has been talking over the agent for
    LIVE_FALLBACK_CUT_SECONDS with no verdict — cut unless the text so far is
    known filler."""
    await asyncio.sleep(LIVE_FALLBACK_CUT_SECONDS)
    while (
        state.caller_speaking
        and not state.utterance_text.strip()
        and time.monotonic() - state.t_speech_started < LIVE_NO_TEXT_CUT_SECONDS
        and not _live_text_stalled(state)
    ):
        await asyncio.sleep(0.1)  # no transcript yet: lag, not a turn
    if not state.caller_speaking or state.live_llm_said_filler:
        return
    if turn_controller.classify_partial(state.utterance_text, settings.voice_smart_turn_taking) == "filler":
        state.live_stats.fallback_filler_kept += 1
        return
    if turn_controller.is_short_burst(state.utterance_text):
        state.live_stats.fallback_filler_kept += 1  # a murmur of agreement: keep talking, answer it afterwards
        return
    if settings.voice_smart_turn_taking and turn_controller.is_candidate_backchannel(
        state.utterance_text, time.monotonic() - state.t_speech_started
    ):
        state.live_stats.fallback_filler_kept += 1  # short, quick and not a clear ask: treat as a soft acknowledgement
        return
    await _cut_now(oai_ws, call_ws, state, log, "live_fallback")


def _drop_filler_reply(state: CallState) -> None:
    """The caller's turn was only a filler: don't answer it after the agent
    finishes talking."""
    if not state.caller_speaking:
        if not state.backchannel_marked:
            # The agent kept talking through an "ok"/"haan": record it as a non-interruptive back-channel.
            state.backchannel_marked = True
            state.latency.event("non_interruptive_backchannel")
        state.live_stats.filler_ignored += 1
        state.reply_pending = False
        state.restate_next = False
        state.interrupted_mid_response = False
        state.priority_reply = False


async def _live_llm_check(oai_ws: Any, call_ws: WebSocket, state: CallState, log: Any) -> None:
    text = state.utterance_text
    token = turn_controller.ACTIVE_STATS.set(state.live_stats)
    try:
        lean = settings.voice_smart_turn_taking and turn_controller.is_candidate_backchannel(
            text, time.monotonic() - state.t_speech_started
        )
        judgement = await turn_controller.judge_turn(
            text, state.openai_api_key, **({"lean_filler": True} if lean else {})
        )
    finally:
        turn_controller.ACTIVE_STATS.reset(token)
    if judgement is not None:
        state.usage.add_judge(judgement.tokens_in, judgement.tokens_out)
        state.live_stats.count_label(judgement.label)
    log.info(
        "voice_live_llm_verdict",
        text=text,
        label=judgement.label if judgement else None,
        confidence=judgement.confidence if judgement else None,
    )
    if judgement is None:
        return
    if time.monotonic() >= state.cut_eval_deadline:
        state.live_stats.judge_late += 1
        return
    verdict = judgement.verdict
    if verdict == "real":
        await _on_real_turn(oai_ws, call_ws, state, log, f"live_llm_{judgement.label.lower()}")
    elif verdict == "filler":
        state.live_llm_said_filler = True
        _drop_filler_reply(state)
    # "wait" (sentence cut off) / "unsure" (low confidence): keep listening —
    # more text re-triggers the check, and the fallback timer covers silence.


async def handle_live_delta(
    delta: str, call_ws: WebSocket, oai_ws: Any, state: CallState, log: Any
) -> None:
    """A partial-transcript fragment of what the caller is saying. While the
    agent is talking, decide from the words so far whether to cut it off."""
    state.utterance_text += delta
    if state.hold_until - time.monotonic() > 0 and not state.caller_speaking:
        # We're holding the reply for a caller who paused mid-sentence: more
        # text has arrived, so re-judge whether they're actually done.
        if turn_controller.endpoint_hold_seconds(state.utterance_text, False, settings.voice_smart_turn_taking) == 0.0:
            _clear_hold(state)
            if state.reply_pending and not state.response_active:
                _schedule_reply(oai_ws, state)
    if time.monotonic() >= state.cut_eval_deadline or not _agent_busy(state):
        return
    verdict = turn_controller.classify_partial(state.utterance_text, settings.voice_smart_turn_taking)
    if verdict == "real":
        log.info("voice_live_verdict", verdict=verdict, text=state.utterance_text)
        await _on_real_turn(oai_ws, call_ws, state, log, "live_real")
    elif verdict == "filler":
        _drop_filler_reply(state)
    elif turn_controller.is_short_burst(state.utterance_text):
        # One or two words with no ask/stop/correction cue: a murmur of agreement in any language. It never
        # reaches the live LLM check; more words re-run this evaluation.
        return
    else:
        words = len(state.utterance_text.split())
        busy = state.live_llm_task is not None and not state.live_llm_task.done()
        if not busy and state.live_llm_calls < LIVE_LLM_MAX_CALLS and words > state.live_llm_last_words:
            state.live_llm_calls += 1
            state.live_llm_last_words = words
            state.live_llm_task = asyncio.create_task(_live_llm_check(oai_ws, call_ws, state, log))


async def _answer_after_readback(oai_ws: Any, state: CallState) -> None:
    """Answer the caller's overlapping questions after the read-back has
    finished playing plus ANSWER_GAP_SECONDS of silence, so they can tell
    where the answer starts."""
    await _wait_until_quiet(state)
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
                        "The caller asked you one or more questions while you were talking (their "
                        "latest spoken turn, already read back to them). Now answer ALL of "
                        "those questions, one after another, fully, in the same language as "
                        f"this sample of the call so far: {_language_sample(state)!r}. Begin "
                        "with a short lead-in that makes it clear the answer is starting, "
                        "e.g. \"Ab aapke sawaal ka jawab:\", and when there is more than one "
                        "question, signal each one (\"pehla sawaal...\", \"doosra sawaal...\")."
                    ),
                },
            }
        )
    )


async def _fire_reply_when_quiet(oai_ws: Any, state: CallState) -> None:
    """Hold the caller's pending reply until the audio already queued to them
    has finished playing, so the next answer starts after a real pause instead
    of stacking behind the previous one."""
    await _wait_until_quiet(state)
    if state.reply_pending and state.ack_stage is None and not state.response_active:
        await _fire_pending_reply(oai_ws, state)


async def _release_hold(oai_ws: Any, state: CallState) -> None:
    """Wait out the endpointing hold, then send the reply if the caller hasn't
    resumed speaking in the meantime."""
    while True:
        remaining = state.hold_until - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    if state.reply_pending and not state.response_active and not state.caller_speaking:
        _schedule_reply(oai_ws, state)


def _trace(state: CallState, kind: str, **fields: Any) -> None:
    """One ``voice_turn_trace`` line: where a turn spent its time (kind, milliseconds, flags). Ids and numbers only;
    deliberately not a latency "event", whose counts feed the turn-taking quality rates. Never raises."""
    try:
        logger.info("voice_turn_trace", call_uuid=state.call_uuid, kind=kind, **fields)
    except Exception:  # noqa: BLE001
        return


def _ms_since(moment: float) -> int | None:
    """Milliseconds since a monotonic moment, or None if it never happened. For trace events only."""
    return round((time.monotonic() - moment) * 1000) if moment else None


def _clear_hold(state: CallState) -> None:
    state.hold_until = 0.0
    if state.hold_task is not None and not state.hold_task.done():
        state.hold_task.cancel()
    state.hold_task = None


def _schedule_reply(oai_ws: Any, state: CallState) -> None:
    """Fire the pending reply now, or once queued audio has drained (or the
    endpointing hold has passed)."""
    if state.hold_until - time.monotonic() > 0:
        if state.hold_task is None or state.hold_task.done():
            _trace(state, "reply_waiting", on="endpoint_hold", wait_ms=round((state.hold_until - time.monotonic()) * 1000))
            state.hold_task = asyncio.create_task(_release_hold(oai_ws, state))
        return
    if _is_playing(state):
        if state.reply_task is None or state.reply_task.done():
            _trace(state, "reply_waiting", on="agent_audio", wait_ms=round(max(0.0, state.playback_end - time.monotonic()) * 1000))
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
    if state.response_active:
        return  # another scheduled task already reserved the response slot
    _trace(state, "reply_firing", ms_since_commit=_ms_since(state.t_committed), restate=bool(state.restate_next))
    state.reply_pending = False
    state.response_active = True
    state.suppress_response_audio = False
    if state.restate_next:
        state.restate_next = False
        state.answer_after_restate = True
        asked = "; ".join(t for t in state.overlap_transcripts if t) or state.utterance_text.strip()
        await oai_ws.send(
            _oob_response(
                "You have just finished your earlier point. In ONE short sentence, tell "
                "the caller what they asked you while you were talking, restating it in "
                "your own words, for example \"aapne poocha tha ki ...\". If they asked "
                "several things, name each one briefly. Use the same language as the "
                "caller's words below. Do NOT answer, explain or give any information "
                "yet - stop right after restating what they asked.",
                f"What the caller said: {asked!r}\n"
                f"Language sample of the call so far: {_language_sample(state)!r}",
            )
        )
        state.overlap_transcripts = []
    else:
        state.overlap_transcripts = []
        state.t_reply_sent = time.monotonic()
        payload: dict[str, Any] = {"type": "response.create"}
        # Decision Layer: if this org is on live voice mode and a decision for
        # what the caller just said is ready within the budget, steer this one
        # reply with it. Anything else (not live, late, error, low confidence)
        # leaves the payload exactly as it has always been.
        response = await _voice_decision_response(state)
        if state.priority_reply:
            state.priority_reply = False
            if settings.voice_smart_turn_taking:
                response = _with_priority(response, state)
        state.answering_transcript = state.pending_user_transcript
        if response is not None:
            payload["response"] = response
        state.latency.mark("response_requested")
        await oai_ws.send(json.dumps(payload))


def _silence_enabled(state: CallState) -> bool:
    """Silence handling only runs for orgs on live voice mode, so every other
    call behaves exactly as it always has."""
    if settings.conversation_silence_check_seconds <= 0 or not settings.conversation_voice_live:
        return False
    return voice_decision.cached_voice_mode(state.org_id)[0] == "live"


async def _prime_voice_mode(org_id: UUID) -> None:
    try:
        await voice_decision.org_voice_mode(org_id)
    except Exception:  # noqa: BLE001 - a failed lookup just leaves silence handling off
        logger.warning("voice_mode_prime_failed", exc_info=True)


def _hold_from_words(state: CallState) -> None:
    """The decision plan did not arrive for this turn: keep the booking/closing guards on, from the words alone,
    for an org already known to be on the enhanced dialogue. Never raises."""
    if not state.dialogue_enhanced:
        return
    try:
        text = state.pending_user_transcript or state.voice_decision_text or ""
        state.tool_holds = dialogue.text_tool_holds(
            text, meeting_declined=state.meeting_declined, today=datetime.now(UTC).date()
        )
        if dialogue.DECLINES_MEETING in dialogue.analyse(text, memory={}, today=datetime.now(UTC).date()).signals:
            state.meeting_declined = True
    except Exception:  # noqa: BLE001 - a guard helper must not break the call
        logger.warning("voice_hold_from_words_failed", exc_info=True)


def _arm_silence(oai_ws: Any, state: CallState, log: Any) -> None:
    """The agent just finished a response: start (or restart) the silence clock."""
    if voice_decision.cached_voice_mode(state.org_id)[0] is None:
        # First response of the call (the greeting): learn the org's voice mode
        # in the background so the next one can start the silence clock.
        asyncio.create_task(_prime_voice_mode(state.org_id))
        return
    if not _silence_enabled(state):
        return
    if state.silence is None:
        state.silence = SilenceMonitor(
            check_after=settings.conversation_silence_check_seconds,
            max_checks=settings.conversation_silence_max_checks,
        )
    state.silence.agent_finished(
        max(time.monotonic(), state.playback_end),
        patience=_THINKING_PATIENCE if settings.voice_smart_turn_taking and state.caller_thinking else 1.0,
    )
    if state.silence_task is None or state.silence_task.done():
        state.silence_task = asyncio.create_task(_silence_loop(oai_ws, state, log))


def _caller_is_active(state: CallState) -> None:
    if state.silence is not None:
        state.silence.caller_active()


_SILENCE_CHECK_REQUEST = (
    "The caller has been quiet for a while, most likely thinking. Say ONE short, relaxed sentence "
    "inviting them to continue when ready, for example \"aap araam se sochiye, main yahin hoon\". "
    "Never mention a connection, a bad line or whether they can hear you. Say it in the same language "
    "as the sample below. Do not repeat your last point and do not ask anything else."
)
# Second and later checks must not be a repeat of the first ("hello? hello?"): no question, no pressure.
_SILENCE_CHECK_AGAIN_REQUEST = (
    "The caller is still quiet. Say ONE very short, relaxed sentence saying there is no hurry and you are "
    "here whenever they are ready, for example \"koi jaldi nahi, aap araam se bataiye\". Do NOT say hello, "
    "do NOT ask whether they can hear you, and do not repeat anything you said before. Say it in the "
    "same language as the sample below."
)
# A caller who asked for a moment gets this many times the usual silence before the first check.
_THINKING_PATIENCE = 2.5
_GOODBYE_REQUEST = (
    "The call is ending now. Say ONE short, warm goodbye sentence and nothing else, in the same language "
    "as the sample below. Do not ask anything."
)
_SILENCE_GOODBYE_REQUEST = (
    "The caller has not answered for a long while. Say ONE short, polite sentence: you will "
    "call back at a better time, and goodbye. Never mention a connection problem, a bad line or an "
    "error. Say it in the same language as the sample below. Add nothing else."
)


async def _silence_loop(oai_ws: Any, state: CallState, log: Any) -> None:
    """Poll the silence monitor once a second; never raises into the call."""
    try:
        while state.silence is not None and not state.silence.issue:
            await asyncio.sleep(1.0)
            monitor = state.silence
            if monitor is None or not _silence_enabled(state):
                continue
            busy = (
                _agent_busy(state)
                or state.reply_pending
                or state.caller_speaking
                or state.ack_stage is not None
                or state.transcripts_outstanding > 0
            )
            action = monitor.poll(time.monotonic(), busy=busy)
            if action is None:
                continue
            log.info("voice_silence", action=action, checks=monitor.checks)
            if action != "check":
                request = _SILENCE_GOODBYE_REQUEST
            elif settings.voice_smart_turn_taking and monitor.checks > 1:
                request = _SILENCE_CHECK_AGAIN_REQUEST
            else:
                request = _SILENCE_CHECK_REQUEST
            state.latency.event("silence_prompt", action=action, checks=monitor.checks)
            state.response_active = True
            await oai_ws.send(
                _oob_response(
                    request, f"Language sample of the call so far: {_language_sample(state)!r}"
                )
            )
            await voice_decision.log_silence_event(
                state.org_id, state.user_id, state.conversation_id, kind=action
            )
            if action == "connection_issue" and settings.voice_end_call_enabled:
                # The goodbye has been said and nobody answered: don't leave an open, silent line.
                call_control.request_hangup(state, log, reason="no_response", outcome="no_response")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — silence handling must never break the call
        logger.warning("voice_silence_failed", exc_info=True)


def _cancel_voice_decision(state: CallState) -> None:
    task = state.voice_decision_task
    state.voice_decision_task = None
    if task is not None and not task.done():
        task.cancel()


def _start_voice_decision(state: CallState, text: str) -> None:
    """Begin deciding about the caller's turn in the background. Cheap when the
    org isn't on live voice mode (a cached flag check), and it never raises."""
    if not settings.conversation_voice_live or not text:
        return
    # An org already known not to be on live voice has nothing to decide: no
    # task, no lookup. The flag is re-checked in the background every few
    # seconds, so switching an org to live still takes effect quickly.
    mode, fresh = voice_decision.cached_voice_mode(state.org_id)
    if fresh and mode != "live":
        return
    _cancel_voice_decision(state)
    state.voice_decision_text = text
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = asyncio.create_task(
        voice_decision.compute_voice_plan(state.org_id, state.user_id, state.conversation_id, text)
    )


async def _voice_decision_response(state: CallState) -> dict[str, Any] | None:
    """The per-response overrides for the next ``response.create``, or None."""
    task = state.voice_decision_task
    if task is None:
        return None
    state.voice_decision_task = None
    # The reply only ever waits for an org that is known to be on live voice.
    # For every other org (or one not seen yet) the reply goes out at once, as
    # it always has; a task still running just refreshes the cached setting.
    if voice_decision.cached_voice_mode(state.org_id)[0] != "live":
        return None
    try:
        remaining = max(
            0.0,
            settings.conversation_voice_decision_budget_seconds
            - (time.monotonic() - state.voice_decision_started),
        )
        try:
            plan = await asyncio.wait_for(task, timeout=remaining)
        except TimeoutError:
            task.cancel()  # too late: it must not run an action afterwards
            runtime_stats.record(
                "voice_budget",
                "budget_timeout",
                (time.monotonic() - state.voice_decision_started) * 1000,
            )
            _hold_from_words(state)
            return None
        if plan is None or state.voice_decision_text != state.pending_user_transcript:
            _hold_from_words(state)
            return None  # nothing to apply, or the caller has said something newer
        config = getattr(plan, "config", None)
        if config is not None and config.enhanced_dialogue:
            state.dialogue_enhanced = True
            state.tool_holds = dialogue.tool_holds(plan.understanding, plan.memory)
            if dialogue.DECLINES_MEETING in plan.understanding.signals or plan.memory.get("meeting_declined"):
                state.meeting_declined = True

        async def dispatch(action_id: str, args: dict[str, Any]) -> dict[str, Any]:
            # Through the same de-duplication as the model's own tool calls, so the decision layer and the
            # model can never both book the same slot.
            return await _dispatch_deduped(state, action_id, json.dumps(args), logger)

        reply = await voice_decision.apply_voice_plan(
            plan,
            org_id=state.org_id,
            user_id=state.user_id,
            conversation_id=state.conversation_id,
            text=state.voice_decision_text,
            dispatch=dispatch,
            done_actions=state.voice_actions_done,
        )
        if reply is None:
            return None
        response: dict[str, Any] = {
            "instructions": f"{state.base_instructions}\n\n{reply.instruction}"
        }
        if reply.tool_choice_none:
            response["tool_choice"] = "none"  # the engine already ran the action
        return response
    except Exception:  # noqa: BLE001 — a decision problem must never break the call
        logger.warning("voice_decision_response_failed", exc_info=True)
        return None


END_CALL_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "end_call",
    "description": (
        "End the phone call. Use it ONLY after you have said a brief, natural goodbye, when the "
        "conversation is finished or the caller asked to end it. Never use it while the caller "
        "still wants something. Say nothing after calling it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "enum": ["task_complete", "customer_requested", "no_response"]}
        },
        "required": ["reason"],
    },
}
RECORDING_CONSENT_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "recording_consent",
    "description": (
        "Report the caller's answer to the question whether the call may be recorded. granted=true "
        "ONLY for a clear yes; false for no or anything unclear."
    ),
    "parameters": {
        "type": "object",
        "properties": {"granted": {"type": "boolean"}},
        "required": ["granted"],
    },
}
# Handled by the adapter itself, never by DISPATCH_TABLE.
_CALL_CONTROL_TOOLS = frozenset({"end_call", "recording_consent"})
# Tools whose effect must happen at most once per distinct request in a call.
_STATE_CHANGING_TOOLS = frozenset(
    {
        "book_appointment", "request_callback", "transfer_to_human", "mark_not_interested",
        "initiate_ai_call", "qualify_lead", "capture_lead", "send_whatsapp_message",
        "send_whatsapp_file", "send_whatsapp_template",
    }
)


def realtime_tools(*, consent: bool = False) -> list[dict[str, Any]]:
    """Convert chat-completions tool schemas to the flat Realtime format.

    Chat format nests under ``function``; the Realtime API wants
    ``name`` / ``description`` / ``parameters`` at the top level of each tool.
    ``end_call`` is added only when VOICE_END_CALL_ENABLED, ``recording_consent`` only for
    an org whose policy asks for consent; with neither, the list is exactly what it was.
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
    if settings.voice_end_call_enabled:
        tools.append(END_CALL_TOOL)
    if consent:
        tools.append(RECORDING_CONSENT_TOOL)
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
        # Every inbound call guarantees a Lead exists for this caller — not
        # just when the AI decides to call capture_lead.
        await get_or_create_lead_for_user(
            db, org_id, user.id, name=user.name, phone=user.phone, channel="voice"
        )
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
    raw_message: str | None = None,
) -> dict[str, Any]:
    """Run a Realtime function call through the shared ``DISPATCH_TABLE``.

    Mirrors ``apps.api.core.agent._dispatch_tool`` but owns a fresh DB session
    (the agent's request-scoped session doesn't exist on the voice path).
    ``org_id`` is the call's already-resolved tenant (``CallState.org_id``,
    see ``_resolve_org_id`` in realtime_bridge.py) — threaded through so tool
    handlers write leads/appointments/human-support to the org that actually
    owns the call, instead of falling back to the platform's default org.
    ``raw_message`` is the caller's current-turn transcript (best-effort —
    the caller may have said something several turns before this specific
    function call landed) — passed through so initiate_ai_call's human-
    handoff keyword guard (core/tools.py) can also catch that case on
    voice, the same way it already does on WhatsApp.
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
    try:
        async with AsyncSessionLocal() as db:
            result = await handler(
                db,
                user_id=user_id,
                org_id=org_id,
                channel="voice",
                campaign_target_id=campaign_target_id,
                conversation_id=conversation_id,
                raw_message=raw_message,
                **args,
            )
    except Exception as exc:  # noqa: BLE001
        # A tool handler can raise (most commonly TypeError for a required
        # arg the model's function-call payload left out — more likely right
        # after a language switch or while the model is juggling several
        # queued questions at once). This used to propagate all the way up
        # through handle_openai_event -> pump_openai_to_call's broad
        # except Exception, which ends that pump and tears down the whole
        # live call for what is really just one bad tool call. Fail this one
        # tool call instead, so the call survives it.
        logger.warning("voice_tool_dispatch_failed", name=name, exc_info=True)
        return {"status": "error", "reason": f"tool_execution_failed:{type(exc).__name__}"}
    return result if isinstance(result, dict) else {"status": "ok", "result": str(result)}


_UNSET: Any = object()
# What is stored as the caller's side of a turn when no transcript of it exists. It is a stand-in, not speech.
NO_TRANSCRIPT_PLACEHOLDER = "(voice)"


async def _persist_voice_turn(
    state: CallState, assistant_text: str, *, pending: Any = _UNSET
) -> None:
    """Persist one (user transcript, assistant transcript) pair mid-call.

    ``pending`` is the caller transcript captured when the turn's text
    finished. A non-blocking TTS finish persists later than the old inline
    path, by which time the caller may have said something new, so the pair
    is built from the snapshot and a newer ``pending_user_transcript`` is left
    alone for its own turn.
    """
    snapshot = state.pending_user_transcript if pending is _UNSET else pending
    state.answering_transcript = None
    user_text = snapshot or NO_TRANSCRIPT_PLACEHOLDER
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
    # Conversation engine, log-only: never awaited, never affects the call.
    schedule_voice_shadow(state.org_id, state.user_id, state.conversation_id, user_text)
    if state.pending_user_transcript == snapshot:
        state.pending_user_transcript = None


def _ack_voice(state: CallState) -> tuple[str, str, dict[str, Any]] | None:
    """(voice_id, model_id, voice_settings) for the instant reaction clip, or None when it does not apply."""
    if not settings.voice_instant_ack or state.tts_provider != "elevenlabs":
        return None
    if state.voice is not None:
        return state.voice.voice_id, state.voice.model_id, state.voice.voice_settings
    return platform_voice_id(), platform_model_id(), dict(LEGACY_VOICE_SETTINGS)


async def _send_instant_ack(call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Play a cached micro-reaction ("जी...") right after the caller stops. Best effort and silent on any
    problem; deliberately does not touch playback/latency accounting, so it never looks like real reply audio."""
    voice = _ack_voice(state)
    if voice is None or state.suppress_response_audio or _agent_busy(state):
        return
    if state.utterance_text.strip() and _is_backchannel(state.utterance_text):
        return  # "haan"/"ok" is not a turn that needs a reaction
    clips = instant_ack.clips_for(voice[0], voice[1])
    if not clips:
        return
    clip = clips[state.ack_clip_index % len(clips)]
    state.ack_clip_index += 1
    try:
        await call_ws.send_text(json.dumps(_media_message(state.provider, state.stream_id, clip)))
        log.info("voice_instant_ack_sent")
    except Exception:  # noqa: BLE001
        pass


def _media_message(provider: str, stream_id: str | None, payload: str) -> dict[str, Any]:
    """The provider-specific envelope for one chunk of mu-law audio (see ``_send_media``)."""
    if provider == "twilio":
        return {"event": "media", "streamSid": stream_id, "media": {"payload": payload}}
    return {"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": payload}}


async def _send_media(ws: WebSocket, state: CallState, payload: str) -> None:
    """Send one chunk of model audio back down the provider's WS.

    Plivo and Twilio Media Streams use different envelopes for the same
    mu-law payload — Plivo names the event ``playAudio`` and repeats the
    codec on every message; Twilio names it ``media`` and requires the
    ``streamSid`` it handed us in its own ``start`` event on every message
    instead.
    """
    await ws.send_text(json.dumps(_media_message(state.provider, state.stream_id, payload)))
    # base64 -> raw byte count; extend the playback horizon by that much audio.
    now = time.monotonic()
    chunk_seconds = (len(payload) * 3 // 4) / _PLAYBACK_BYTES_PER_SECOND
    state.playback_end = max(state.playback_end, now) + chunk_seconds
    state.resp_audio_seconds += chunk_seconds
    state.latency.mark_first_audio_sent()


async def _forward_elevenlabs_audio(call_ws: WebSocket, state: CallState, log: Any) -> None:
    """Background task: drain one ElevenLabsTTSSession's audio queue onto
    the call as it arrives — started as soon as the first text delta opens
    the session, not only after the full response finishes, so playback
    starts as early as possible."""
    session = state.eleven_session
    if session is None:
        return
    generation = state.generation
    try:
        async for chunk in session.audio_chunks():
            if state.generation != generation:
                return  # the caller cut this turn off: nothing more of it may play
            state.eleven_turn_audio_chunks += 1
            if state.eleven_turn_audio_chunks == 1:
                # Characters ElevenLabs had been given when its first audio came back. It starts generating at
                # a character threshold (default ~120) or at the end of the turn, so this separates "the
                # provider was slow" from "it was waiting for enough text". A count only, never the text.
                sent_chars = getattr(session, "chars_sent", None)
                if isinstance(sent_chars, int):  # measurement only: it must never be able to stop the audio
                    state.latency.note(tts_chars_at_first_chunk=sent_chars)
            state.latency.mark("tts_first_chunk")
            await _send_media(call_ws, state, chunk)
        if (
            settings.voice_tts_recovery
            and session is state.eleven_session
            and state.generation == generation
            and not session.final_seen
        ):
            # The audio stream ended without ElevenLabs' final marker: it errored or dropped.
            _spawn_tts_recovery(state, log, session.fail_reason or "stream_ended_early")
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("elevenlabs_audio_forward_error", exc_info=True)


async def _close_eleven_session(state: CallState, session: Any = None) -> None:
    """Close the turn's forwarder and ElevenLabs session. With ``session``
    given, does nothing unless it is still the call's current one, so a late
    cleanup can never close a newer turn's session."""
    if session is not None and state.eleven_session is not session:
        return
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


async def _discard_prewarm(state: CallState) -> None:
    """Cancel an unclaimed prewarm and close the socket it opened (if it got
    that far). Safe when there is none."""
    task, state.eleven_prewarm_task = state.eleven_prewarm_task, None
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.wait({task})
    if not task.cancelled() and task.exception() is None:
        session = task.result()
        if session is not None:
            await session.__aexit__(None, None, None)


async def _teardown_elevenlabs_turn(state: CallState) -> None:
    """Tear down any in-flight ElevenLabs session/forward task: called both
    to cancel mid-speech on barge-in and to clean up after a turn finishes
    normally (the forward task is already done by then, so cancel() is a
    harmless no-op in that case). Also cancels a pending non-blocking finish
    and an unclaimed prewarm, so an interrupted turn cannot resume or leak."""
    finalize, state.eleven_finalize_task = state.eleven_finalize_task, None
    if finalize is not None and finalize is not asyncio.current_task() and not finalize.done():
        finalize.cancel()
        await asyncio.wait({finalize})
    watchdog, state.tts_watchdog_task = state.tts_watchdog_task, None
    if watchdog is not None and watchdog is not asyncio.current_task() and not watchdog.done():
        watchdog.cancel()
    await _discard_prewarm(state)
    await _close_eleven_session(state)


async def _open_elevenlabs_session(
    state: CallState, log: Any, *, safe: bool = False, retry: bool = False
) -> elevenlabs_client.ElevenLabsTTSSession:
    """Open this turn's socket. ``safe=True`` ignores the org's voice profile (platform voice/model
    and default settings, which are known to work) - used by TTS recovery after a failure."""
    state.latency.mark("tts_connect_started")
    if settings.voice_tts_engine == "sarvam" and not safe and sarvam_client.is_configured():
        session_cm: Any = sarvam_client.SarvamTTSSession()
    else:  # ``safe`` (recovery after a failure) always uses ElevenLabs
        session_cm = (
            elevenlabs_client.ElevenLabsTTSSession(profile=state.voice)
            if (state.voice is not None and not safe)
            else elevenlabs_client.ElevenLabsTTSSession()
        )
    session = await session_cm.__aenter__()
    # Characters sent are accounted when the session closes (a retry's are shown separately).
    session.on_close = lambda chars, _retry=retry: state.usage.add_tts_chars(chars, retry=_retry)
    state.latency.mark("tts_ready")
    log.info("elevenlabs_session_ready", connect_ms=session.connect_ms)
    if session.connect_ms is not None:
        state.latency.metric("tts_connect_ms", session.connect_ms, retry=retry)
    if settings.voice_tts_recovery and not safe:
        # While ElevenLabs is demonstrably working, keep an apology ready in case it stops.
        recovery.fallback_audio.ensure_in_background(state.voice)
    return session


def _start_prewarm(state: CallState, log: Any) -> None:
    """VOICE_ELEVENLABS_PREWARM: begin opening this turn's ElevenLabs socket
    now, so the connect overlaps OpenAI's time-to-first-token. The socket is
    still single-use: it is claimed by this response's first text delta, or
    discarded when the response ends without speaking (e.g. a tool call)."""
    if (
        not settings.voice_elevenlabs_prewarm
        or state.tts_provider != "elevenlabs"
        or state.suppress_response_audio
        or state.eleven_session is not None
        or state.eleven_prewarm_task is not None
        or _finalizing(state)
        or not elevenlabs_client.is_configured()
    ):
        return

    async def _open() -> elevenlabs_client.ElevenLabsTTSSession | None:
        try:
            return await _open_elevenlabs_session(state, log)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the first delta just opens one itself
            log.warning("elevenlabs_prewarm_failed", error_type=type(exc).__name__)
            return None

    state.eleven_prewarm_task = asyncio.create_task(_open())


async def _close_when_ready(task: "asyncio.Task[Any]") -> None:
    try:
        await asyncio.wait({task})
        if not task.cancelled() and task.exception() is None and task.result() is not None:
            await task.result().__aexit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


async def _take_elevenlabs_session(
    state: CallState, log: Any
) -> elevenlabs_client.ElevenLabsTTSSession:
    """The session for this turn: the prewarmed one if it is (or becomes)
    ready, otherwise a fresh connection exactly as before prewarming existed."""
    task, state.eleven_prewarm_task = state.eleven_prewarm_task, None
    if task is not None:
        try:
            await asyncio.wait({task})  # a cancel by teardown is not our own cancellation
        except asyncio.CancelledError:
            # We were cancelled (e.g. a connect timeout) while the prewarm was still connecting:
            # whatever it opens must not be left behind.
            asyncio.create_task(_close_when_ready(task))
            raise
        if not task.cancelled() and task.exception() is None:
            session = task.result()
            if session is not None:
                state.latency.note(tts_prewarmed=True)
                return session
    return await _open_elevenlabs_session(state, log)


async def _finish_elevenlabs_turn(
    state: CallState,
    session: elevenlabs_client.ElevenLabsTTSSession,
    fallback_text: str,
    pending_transcript: str | None,
    log: Any,
) -> None:
    """Flush the turn's text to ElevenLabs, let the rest of its audio reach the
    caller, then close and persist. Run inline (the original behaviour) or as
    ``state.eleven_finalize_task`` (VOICE_NONBLOCKING_TTS_FINISH)."""
    generation = state.generation
    try:
        await session.finish()
        if state.eleven_forward_task is not None and state.eleven_session is session:
            await state.eleven_forward_task
        if (
            settings.voice_tts_recovery
            and state.generation == generation
            and any(ch.isalnum() for ch in fallback_text)
            and state.eleven_turn_audio_chunks == 0
        ):
            # Text was spoken to ElevenLabs and not one frame of audio came back.
            _spawn_tts_recovery(state, log, "no_audio_for_turn")
            return
        await _close_eleven_session(state, session)
        if fallback_text:
            await _persist_voice_turn(state, fallback_text, pending=pending_transcript)
            log.info("voice_assistant_transcript", text=fallback_text)
    except asyncio.CancelledError:
        raise  # the interrupter (_teardown_elevenlabs_turn) closes everything
    except Exception:  # noqa: BLE001 - one bad turn must not break the call
        log.warning("elevenlabs_finish_failed", exc_info=True)
        await _close_eleven_session(state, session)


async def cancel_pending_tasks(state: CallState) -> None:
    """Cancel every background task that still holds a reference to the
    OpenAI socket realtime_bridge.py is about to replace (a reconnect after
    the Realtime session dropped mid-call — see that module's reconnect
    loop). Each of these was created with the *old* ``oai_ws`` closed over,
    so left running they'd try to ``.send()`` on a dead connection instead
    of picking up the fresh one. Turn-state fields (ack_stage etc.) are reset
    by the caller after this; here we only stop the tasks themselves."""
    for attr in (
        "cut_task",
        "ack_task",
        "hold_task",
        "reply_task",
        "live_llm_task",
        "silence_task",
        "cancel_task",
        "tts_watchdog_task",
        "tts_recovery_task",
    ):
        task = getattr(state, attr)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        setattr(state, attr, None)
    await _teardown_elevenlabs_turn(state)
    state.eleven_text_buffer = ""


# Cues that indicate the caller is explicitly asking the agent to change
# what language it speaks — as opposed to merely saying a sentence that
# happens to be in (or names) some language. This is deliberately about
# *intent* only, not the target language: the target is resolved afterwards
# by language_detect.detect_caller_language (name match -> script match ->
# LLM fallback), which already covers all _CANDIDATE_LANGUAGES, not just
# Hindi/English — this regex just has to recognise the "switch"/"speak
# in"/"X mein baat karo" shape, in whichever script the caller (or Whisper's
# transcription of them) used to say it. Covers Latin-script Hindi/English
# phrasing plus the Devanagari equivalent, since Hindi/Marathi/Nepali
# speakers are commonly transcribed in Devanagari rather than Latin.
_LANGUAGE_SWITCH_CUE_RE = re.compile(
    r"\b(speak|talk|reply|answer)\b[^.!?]{0,25}\bin\b"           # "speak in Hindi"
    r"|\bswitch\b[^.!?]{0,15}\b(to|karo|kar\s*do)\b"              # "switch to English" / "switch karo"
    r"|\bmein\s+(baat|bol|bolo|bolna|bat|jawab)"                  # "hindi mein baat karo/bolo"
    r"|\b(badal|badlo|change)\b[^.!?]{0,15}\blanguage\b"          # "language change karo"
    r"|में\s*(बात|बोल|जवाब)"  # Devanagari "में बात/बोल/जवाब"
    r"|भाषा\s*(बदल|बदलो)",               # Devanagari "भाषा बदल(ो)"
    re.IGNORECASE,
)


async def _handle_language_switch_request(oai_ws: Any, state: CallState, text: str, log: Any) -> None:
    """Background task kicked off the moment a transcript matches
    _LANGUAGE_SWITCH_CUE_RE: resolve which language the caller meant and, if
    resolved, reinforce it on the live session. Runs as a task so the LLM
    fallback inside detect_switch_target never stalls the hot
    event-processing path. Uses
    language_detect.detect_switch_target rather than detect_caller_language:
    the latter answers "what language is this text in", which is the wrong
    question when a switch request names one language while being phrased in
    another (e.g. "बांग्ला में बात करो" is grammatically Hindi but asks for
    Bengali) — covers any of the 24 candidate languages either way, named in
    Latin script or in their own native script."""
    language = await language_detect.detect_switch_target(text)
    if language is None:
        log.info("voice_language_switch_unresolved", text=text)
        return
    await _apply_language_switch(oai_ws, state, language, log)


async def _apply_language_switch(oai_ws: Any, state: CallState, language: str, log: Any) -> None:
    """The caller explicitly asked (mid-call) to switch language. Reinforce
    it with a real session.update immediately, rather than leaving it to
    prose in the system prompt alone — this is what makes the switch take
    effect on the very next reply instead of sometimes needing to be said
    twice, and keeps state.base_instructions current so later one-off
    responses (e.g. _answer_after_readback) don't regenerate in the old
    language."""
    updated_instructions = (
        f"{state.base_instructions}\n\n"
        f"The caller just explicitly asked you to switch languages. Speak "
        f"{language} starting with your very next reply, for the rest of the "
        f"call, unless they ask to switch again."
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
        state.base_instructions = updated_instructions
        log.info("voice_language_switch_applied", language=language)
    except Exception:  # noqa: BLE001
        log.warning("voice_language_switch_send_failed", exc_info=True)


async def _acquire_tts_session(state: CallState, log: Any) -> elevenlabs_client.ElevenLabsTTSSession:
    """The turn's ElevenLabs session. With TTS recovery on, the connect is bounded, so a hung
    handshake becomes a recoverable failure instead of a silent call."""
    if not settings.voice_tts_recovery:
        return await _take_elevenlabs_session(state, log)
    timeout = call_control.bounded(settings.voice_tts_connect_timeout_seconds, 0.5, 30.0, 4.0)
    return await asyncio.wait_for(_take_elevenlabs_session(state, log), timeout)


def _start_tts_watchdog(state: CallState, log: Any) -> None:
    """If text has gone to ElevenLabs and no audio has come back after the (bounded) timeout,
    treat the turn as failed rather than letting the caller listen to silence."""
    if not settings.voice_tts_recovery or state.eleven_session is None:
        return
    if state.tts_watchdog_task is not None and not state.tts_watchdog_task.done():
        return
    session = state.eleven_session
    state.tts_last_text_at = state.tts_last_text_at or time.monotonic()

    async def _watch() -> None:
        try:
            timeout = call_control.bounded(settings.voice_tts_first_audio_timeout_seconds, 1.0, 60.0, 6.0)
            # ElevenLabs may legitimately wait for a clause of text before it speaks, so a model that is still
            # producing text is not a stalled provider: the clock restarts whenever more text goes out.
            while True:
                remaining = state.tts_last_text_at + timeout - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)
            if (
                state.eleven_session is session
                and state.eleven_turn_audio_chunks == 0
                and not state.tts_failed_over
            ):
                _spawn_tts_recovery(state, log, "no_audio_timeout")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.warning("tts_watchdog_failed", exc_info=True)

    state.tts_watchdog_task = asyncio.create_task(_watch())


def _spawn_tts_recovery(state: CallState, log: Any, reason: str) -> None:
    """Run TTS recovery as its own task (it tears down the very task that noticed the failure).
    One recovery at a time; a no-op unless VOICE_TTS_RECOVERY is on."""
    if not settings.voice_tts_recovery or state.tts_failed_over:
        return
    if state.tts_recovery_task is not None and not state.tts_recovery_task.done():
        return
    state.tts_recovery_task = asyncio.create_task(_tts_failed(state, log, reason))


async def _tts_failed(state: CallState, log: Any, reason: str) -> None:
    """A turn's speech failed. Retry once with safe settings (replaying the turn), then fall back
    to the pre-generated apology and a controlled end. See channels/voice/recovery.py."""
    try:
        state.tts_failures += 1
        log.warning("voice_tts_failure", reason=reason, failures=state.tts_failures)
        text_so_far = state.eleven_text_buffer
        text_done = state.tts_text_done
        await _teardown_elevenlabs_turn(state)
        maximum = int(call_control.bounded(settings.voice_tts_max_failures_per_call, 1, 10, 2))
        if state.tts_failures <= maximum and not state.tts_turn_retried and text_so_far.strip():
            state.tts_turn_retried = True
            timeout = call_control.bounded(settings.voice_tts_connect_timeout_seconds, 0.5, 30.0, 4.0)
            try:
                session = await asyncio.wait_for(
                    _open_elevenlabs_session(state, log, safe=True, retry=True), timeout
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("voice_tts_retry_failed", error_type=type(exc).__name__)
            else:
                state.eleven_turn_audio_chunks = 0
                # Deltas that arrive while we replay are buffered by the handler (recovery is pending),
                # so assigning the session now is safe and lets the forwarder find it.
                state.eleven_session = session
                state.eleven_forward_task = asyncio.create_task(
                    _forward_elevenlabs_audio(state.call_ws, state, log)
                )
                sent = 0
                while True:  # deltas that arrived while we were reconnecting are picked up here
                    buffered = state.eleven_text_buffer
                    if sent >= len(buffered):
                        break
                    chunk, sent = buffered[sent:], len(buffered)
                    await session.send_text(chunk)
                if text_done:
                    state.eleven_finalize_task = asyncio.create_task(
                        _finish_elevenlabs_turn(
                            state, session, state.tts_final_text, state.tts_final_pending, log
                        )
                    )
                else:
                    _start_tts_watchdog(state, log)
                log.info("voice_tts_recovered", reason=reason)
                return
        await _tts_fallback(state, log, reason)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - recovery must end in *something*, never an exception
        log.warning("voice_tts_recovery_failed", exc_info=True)
        call_control.request_hangup(state, log, reason="tts_failure", outcome="tts_failure", abortable=False)


async def play_fallback_apology(state: CallState, log: Any) -> bool:
    """Stop whatever is playing and play the pre-generated apology (made earlier in the call, in this
    org's voice) if there is one. True if it was played. Never raises."""
    call_ws = state.call_ws
    try:
        if call_ws is None:
            return False
        await _send_clear(call_ws, state)
        audio = recovery.fallback_audio.get(state.voice)
        if not audio:
            log.warning("voice_fallback_audio_missing")
            return False
        for chunk in audio:
            await _send_media(call_ws, state, chunk)
        return True
    except Exception:  # noqa: BLE001
        log.warning("voice_fallback_playback_failed", exc_info=True)
        return False


async def _tts_fallback(state: CallState, log: Any, reason: str) -> None:
    """Out of retries: stop speaking, play the pre-generated apology if there is one, raise a human
    follow-up and end the call in a controlled way. Never leaves the line silent and open."""
    state.tts_failed_over = True
    state.suppress_response_audio = True
    state.reply_pending = False
    await play_fallback_apology(state, log)
    try:
        await _dispatch_deduped(
            state,
            "transfer_to_human",
            json.dumps({"reason": recovery.FOLLOW_UP_REASON, "urgency": "high"}),
            log,
        )
    except Exception:  # noqa: BLE001
        log.warning("voice_tts_follow_up_failed", exc_info=True)
    log.warning("voice_tts_failed_over", reason=reason)
    call_control.request_hangup(state, log, reason="tts_failure", outcome="tts_failure", abortable=False)


def _action_key(name: str, args_json: str) -> str:
    """Identity of a state-changing action within a call. A booking or callback is the same action when
    it is for the same slot, whoever asked for it (the model, or the decision layer), whatever the notes."""
    try:
        args = json.loads(args_json or "{}")
    except (TypeError, ValueError):
        return f"{name}|{args_json or ''}"
    if name in ("book_appointment", "request_callback") and isinstance(args, dict) and args.get("date") and args.get("time"):
        return f"{name}|{args['date']}|{args['time']}"
    return f"{name}|{_canonical_args(args_json)}"


def _canonical_args(args_json: str) -> str:
    try:
        return json.dumps(json.loads(args_json or "{}"), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return args_json or ""


async def _dispatch_deduped(state: CallState, name: str, args_json: str, log: Any) -> dict[str, Any]:
    """Run a Realtime tool call. With VOICE_RECONNECT_RECOVERY, a state-changing tool that already
    succeeded with the same arguments earlier in this call is not run again: its earlier result is
    returned. That is what stops a duplicate appointment, message or CRM write after the model
    repeats itself or a reconnect replays the conversation."""
    hold = state.tool_holds.get(name)
    if hold:
        # Enforced here, not just in the prompt: the model may still try to book on a guess or after a refusal.
        log.info("voice_tool_held", tool=name)
        return {
            "status": "error",
            "reason": "held_by_dialogue_policy",
            "note": f"Not done: {hold}. Do not tell the caller it was done.",
        }
    key = None
    if settings.voice_reconnect_recovery and name in _STATE_CHANGING_TOOLS:
        key = _action_key(name, args_json)
        earlier = state.action_results.get(key)
        if earlier is not None:
            log.info("voice_tool_duplicate_suppressed", tool=name)
            return {**earlier, "duplicate": True}
    result = await _dispatch_realtime_tool(
        name,
        args_json,
        state.user_id,
        state.org_id,
        campaign_target_id=state.campaign_target_id,
        conversation_id=state.conversation_id,
        raw_message=state.pending_user_transcript,
    )
    if key is not None and isinstance(result, dict) and result.get("status") == "ok":
        state.action_results[key] = result
        state.completed_actions.append(name)
    return result


async def _handle_call_control_tool(
    name: str, args_json: str, call_id: str | None, oai_ws: Any, state: CallState, log: Any
) -> None:
    """``end_call`` and ``recording_consent``: handled here, never by the shared tool table."""
    try:
        args = json.loads(args_json or "{}")
    except (TypeError, ValueError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    ends_call = False
    say_goodbye = False
    if name == "end_call":
        if not settings.voice_end_call_enabled:
            result: dict[str, Any] = {"status": "error", "reason": "end_call_not_enabled"}
        else:
            reason = args.get("reason") if args.get("reason") in ("task_complete", "customer_requested", "no_response") else "task_complete"
            outcome = {"customer_requested": "customer_requested_end", "no_response": "no_response"}.get(
                reason, "completed"
            )
            call_control.request_hangup(
                state, log, reason=reason, outcome=outcome, abortable=reason != "customer_requested",
                after_response=not state.resp_text.strip(),
            )
            ends_call = True
            result = {"status": "ok", "note": "The call ends once your goodbye has finished playing. Say nothing more."}
            if not state.resp_text.strip():
                # end_call came with nothing spoken in this response: the caller must not be cut off in
                # silence, so have the agent say a brief goodbye first and hang up after THAT.
                say_goodbye = True
    else:
        result = await _handle_recording_consent(state, args.get("granted") is True, log)
    await oai_ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result)},
            }
        )
    )
    if say_goodbye:
        state.tool_goodbye_pending = True
    elif not ends_call:
        state.tool_response_pending = True
    log.info("voice_call_control_tool", tool=name)


async def _handle_recording_consent(state: CallState, granted: bool, log: Any) -> dict[str, Any]:
    from apps.api.channels.voice import compliance

    if not state.consent_tool:
        return {"status": "error", "reason": "consent_not_requested_for_this_call"}
    started = False
    if granted and not state.recording_started:
        started = await call_control.start_call_recording(state, log)
        state.recording_started = started
    try:
        async with AsyncSessionLocal() as db:
            await compliance.record_consent_outcome(db, state.conversation_id, state.org_id, granted)
    except Exception:  # noqa: BLE001
        log.warning("voice_consent_record_failed", exc_info=True)
    return {"status": "ok", "recording": "started" if (granted and state.recording_started) else "not_recording"}


async def build_reconnect_items(state: CallState) -> list[dict[str, Any]]:
    """The last few turns of THIS conversation (same org) as Realtime conversation items, so a
    fresh session can carry on. Text only: no tool calls are replayed (that is how a duplicate
    booking would happen), long numbers/OTPs/e-mails are masked, and each turn is truncated."""
    count = int(call_control.bounded(settings.voice_reconnect_context_turns, 0, 20, 6))
    if count == 0:
        return []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Message)
                .where(Message.org_id == state.org_id, Message.conversation_id == state.conversation_id)
                .order_by(Message.created_at.desc())
                .limit(count)
            )
        ).scalars().all()
    items: list[dict[str, Any]] = []
    for row in reversed(rows):
        if row.role != "assistant" and (row.content or "").strip() == NO_TRANSCRIPT_PLACEHOLDER:
            continue  # a stand-in for a caller turn nobody transcribed: replaying it would invent a line they never said
        text = privacy.redact_text(row.content or "")[:500].strip()
        if not text:
            continue
        assistant = row.role == "assistant"
        items.append(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "assistant" if assistant else "user",
                    "content": [{"type": "output_text" if assistant else "input_text", "text": text}],
                },
            }
        )
    # What the caller said last but has not been answered yet is not in the database (turns are saved once the agent
    # has replied), so without this the new session would not know what it is being asked to answer.
    pending = privacy.redact_text(state.pending_user_transcript or "")[:500].strip()
    if pending and pending != NO_TRANSCRIPT_PLACEHOLDER:
        last_user = next((i for i in reversed(items) if i["item"]["role"] == "user"), None)
        already = last_user is not None and last_user["item"]["content"][0]["text"] == pending
        if not already:
            items.append(
                {
                    "type": "conversation.item.create",
                    "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": pending}]},
                }
            )
    return items


def reconnect_resume_instruction(state: CallState) -> str:
    done = ", ".join(dict.fromkeys(state.completed_actions)) or "none"
    return (
        "The connection dropped for a moment and has been restored. Carry on naturally from the "
        "conversation so far: do NOT greet again, do not repeat anything you already said, and if the "
        "caller's last request was not answered, answer it now. Actions already completed during this "
        f"call (never do them again): {done}."
    )


@dataclass(frozen=True)
class EarlyGreeting:
    """A cached greeting whose audio has already gone to the caller, before the call's state existed."""

    text: str
    playback_end: float  # time.monotonic() when the audio finishes playing
    chunks: int


class EarlyGreetingPartial(Exception):
    """Some of the greeting audio was sent before something failed: it cannot be taken back."""

    def __init__(self, early: EarlyGreeting) -> None:
        super().__init__("partial early greeting")
        self.early = early


async def send_cached_audio_early(
    call_ws: WebSocket, provider: str, stream_id: str | None, text: str, chunks: Any
) -> EarlyGreeting:
    """Send pre-synthesized greeting audio straight down the provider's socket. Needs only the socket, the provider and the
    stream id, so it can run while the conversation row and the model session are still being prepared. A disconnect
    propagates (the call is gone); any other failure after audio went out raises EarlyGreetingPartial."""
    end, sent = time.monotonic(), 0
    try:
        for chunk in chunks:
            await call_ws.send_text(json.dumps(_media_message(provider, stream_id, chunk)))
            end = max(end, time.monotonic()) + (len(chunk) * 3 // 4) / _PLAYBACK_BYTES_PER_SECOND
            sent += 1
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        if sent:
            raise EarlyGreetingPartial(EarlyGreeting(text, end, sent)) from exc
        raise
    return EarlyGreeting(text, end, sent)


async def adopt_early_greeting(state: CallState, oai_ws: Any, early: EarlyGreeting, log: Any) -> None:
    """Make the call's state and the model session consistent with a greeting that is already playing: the model is told
    what was said (so it never greets again), the callee's reflexive "hello?" cannot cut it, and it is persisted."""
    state.greeting_guard_extended = True
    state.playback_end = max(state.playback_end, early.playback_end)
    state.greeting_guard_until = state.playback_end
    state.last_spoken_text = early.text
    await oai_ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": early.text}]},
            }
        )
    )
    await _persist_voice_turn(state, early.text, pending=None)
    log.info("voice_greeting_cached_adopted", chunks=early.chunks)


async def play_cached_greeting(
    call_ws: WebSocket, oai_ws: Any, state: CallState, text: str, chunks: Any, log: Any
) -> None:
    """Play a pre-synthesized greeting (channels/voice/greeting.py's cache)
    instead of asking the model to speak it.

    The Realtime session never produced this audio, so it is told what was
    said by adding it to the conversation as an assistant message; without
    that the model would not know it already greeted and could do so again.
    UNVERIFIED against a live session: the item shape follows the Realtime
    ``conversation.item.create`` schema, and the feature is opt-in
    (VOICE_GREETING_AUDIO_CACHE) until a real call confirms it.
    """
    state.latency.begin_turn(kind="greeting_cached")
    state.latency.mark("response_requested")
    state.greeting_guard_until = time.monotonic() + 10.0
    await oai_ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
            }
        )
    )
    for chunk in chunks:
        await _send_media(call_ws, state, chunk)
    # Same protection a spoken greeting gets: the callee's reflexive "hello?"
    # must not cut it; lifted once its audio has finished playing.
    state.greeting_guard_extended = True
    state.greeting_guard_until = state.playback_end
    state.last_spoken_text = text
    await _persist_voice_turn(state, text, pending=None)
    log.info("voice_greeting_cached_played", chunks=len(chunks))


async def handle_openai_event(
    event: dict[str, Any],
    call_ws: WebSocket,
    oai_ws: Any,
    state: CallState,
    log: Any,
) -> None:
    """Translate one OpenAI Realtime event into provider actions / tool calls."""
    etype = event.get("type")

    # Anything a cancelled response still emits (audio, text, a tool call) is
    # discarded: the caller must never hear it or have it acted on.
    if state.cancelled_response_ids and etype in _RESPONSE_CONTENT_EVENTS and _is_stale_response(state, event):
        log.info("voice_stale_response_event_dropped", etype=etype)
        state.latency.event("stale_event_dropped", etype=etype)
        return

    # GA renamed several beta event types (response.audio.delta ->
    # response.output_audio.delta, etc.) but docs are inconsistent on the
    # exact final names post-migration — accept both until confirmed live.
    if etype in ("response.audio.delta", "response.output_audio.delta"):
        delta = event.get("delta")
        if delta and state.suppress_response_audio:
            state.latency.event("muted_audio_dropped")
        if delta and not state.suppress_response_audio:
            state.latency.mark("openai_first_output")
            await _send_media(call_ws, state, delta)
            _note_first_output(state, log, "audio")

    elif etype == "response.output_item.added":
        item = event.get("item") or {}
        rid = event.get("response_id") or (event.get("response") or {}).get("id")
        if rid and item.get("id") and item.get("type") == "message":
            state.response_items.setdefault(rid, []).append(item["id"])
            while len(state.response_items) > 20:
                state.response_items.pop(next(iter(state.response_items)))

    elif etype in ("conversation.item.added", "conversation.item.created"):
        item = event.get("item") or {}
        if item.get("id"):
            state.item_previous[item["id"]] = event.get("previous_item_id")
            while len(state.item_previous) > 60:
                state.item_previous.pop(next(iter(state.item_previous)))

    elif etype == "input_audio_buffer.speech_started":
        state.t_speech_started = time.monotonic()
        ack = _ack_voice(state)
        if ack is not None:
            instant_ack.warm(*ack)  # synthesize the clips while the caller is still talking
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
        _caller_is_active(state)
        if (
            state.response_active and state.t_reply_sent and state.resp_audio_seconds <= 0
            and time.monotonic() - state.t_reply_sent < 2.0
        ):
            # We answered, and the caller started speaking again before any of the answer was heard:
            # we probably took their pause for the end of their turn.
            state.latency.event(
                "premature_response_suspected", since_reply_ms=round((time.monotonic() - state.t_reply_sent) * 1000)
            )
        call_control.abort_hangup(state, log)
        _clear_hold(state)
        # OpenAI's VAD can fire speech_started more than once for what is
        # really one continuous overlap (a brief gap it reads as a new
        # segment). Wiping the accumulated live-transcript text/verdict state
        # on every one of those bursts was resetting the judge's progress
        # back to a short fragment each time, so it kept re-classifying "I
        # already have tele..." as CONTINUATION forever and never reached a
        # confident "real" verdict before the agent's answer finished on its
        # own. Only reset when this is a genuinely new overlap episode
        # (interrupted_mid_response is cleared once the episode resolves —
        # see _on_real_turn/_cut_now/input_audio_buffer.committed).
        if not state.interrupted_mid_response:
            state.backchannel_marked = False
            state.utterance_text = ""
            state.live_llm_calls = 0
            state.live_llm_last_words = 0
            state.live_llm_said_filler = False
        state.finish_first_pending = False
        if _agent_busy(state):
            state.interrupted_mid_response = True
            state.latency.interruption_detected()
            log.info("voice_caller_spoke_over_response", live=_live_ready(state))
            if settings.voice_native_barge_in:
                await _cut_now(oai_ws, call_ws, state, log, "native_barge_in")
            elif _live_ready(state):
                # Judge by the words as they stream in.
                state.cut_eval_deadline = time.monotonic() + 60.0
                if _answer_immediately() and (state.cut_task is None or state.cut_task.done()):
                    # Safety net if no verdict forms.
                    state.cut_task = asyncio.create_task(
                        _live_fallback_cut(oai_ws, call_ws, state, log)
                    )
            elif _answer_immediately() and (state.cut_task is None or state.cut_task.done()):
                state.cut_task = asyncio.create_task(
                    _cut_if_still_speaking(oai_ws, call_ws, state, log)
                )

    elif etype == "input_audio_buffer.speech_stopped":
        state.caller_speaking = False
        state.t_speech_stopped = time.monotonic()
        if not (state.greeting_guard_until and state.t_speech_stopped < state.greeting_guard_until):
            state.latency.begin_turn()
            state.latency.mark("speech_stopped")
            if _ack_voice(state) is not None:
                asyncio.create_task(_send_instant_ack(call_ws, state, log))
        if state.cut_eval_deadline:
            state.cut_eval_deadline = min(
                state.cut_eval_deadline, time.monotonic() + LIVE_EVAL_TAIL_SECONDS
            )

    elif etype == "response.created":
        created_id = (event.get("response") or {}).get("id")
        if created_id and (created_id in state.completed_response_ids or created_id == state.response_id):
            log.info("voice_duplicate_response_created_dropped")
            return
        state.response_active = True
        state.resp_text = ""
        state.resp_audio_seconds = 0.0
        state.response_id = (event.get("response") or {}).get("id")
        state.latency.mark("openai_response_created")
        state.latency.interruption_next_response()
        _start_prewarm(state, log)

    elif etype in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
        state.resp_text += event.get("delta") or ""

    elif etype == "input_audio_buffer.committed":
        # create_response=False (session.update above) means nothing else
        # triggers the next response — this is the one place we do, so it's
        # also the one place that can attach a one-off instruction to that
        # specific response without touching the persisted session
        # instructions used every other turn.
        state.transcripts_outstanding += 1
        state.t_committed = time.monotonic()
        state.latency.mark("committed")
        state.latency.note(agent_was_busy=_agent_busy(state))
        if state.interrupted_mid_response:
            state.interrupted_mid_response = False
            state.restate_next = not _answer_immediately()
        state.reply_pending = True
        # "hmm thik hai ok": a chain of acknowledgments to an agent that has finished talking is the caller
        # saying "I'm with you", not asking for anything. Stay silent and let them carry on.
        absorbed = (
            settings.voice_smart_turn_taking
            and _live_ready(state)
            and not _agent_busy(state)
            and turn_controller.is_chained_backchannel(state.utterance_text)
        )
        if absorbed:
            state.reply_pending = False
            log.info("voice_extended_backchannel_absorbed", words=len(state.utterance_text.split()))
            state.latency.event("extended_backchannel_absorbed")
        # Adaptive endpointing: if the text streamed so far looks unfinished,
        # wait a little before answering in case they carry on.
        hold, hold_reason = (
            turn_controller.hold_decision(state.utterance_text, _agent_busy(state), settings.voice_smart_turn_taking)
            if _live_ready(state) and not absorbed and (settings.voice_endpoint_hold or settings.voice_smart_turn_taking)
            else (0.0, None)
        )
        state.hold_until = time.monotonic() + hold if hold else 0.0
        if hold:
            # Reason and duration only: what the caller said does not belong in this log line.
            log.info("voice_endpoint_hold", seconds=hold, reason=hold_reason, words=len(state.utterance_text.split()))
            state.latency.event("endpoint_hold", reason=hold_reason, hold_ms=round(hold * 1000))
        # finish_first: the caller asked something real while the agent talked
        # and has now finished asking — pause, acknowledge, resume, read back.
        if (
            not _answer_immediately()
            and state.finish_first_pending
            and _agent_busy(state)
            and state.ack_stage is None
        ):
            await _start_finish_first(oai_ws, call_ws, state, log)
        # Still speaking the earlier answer: hold the reply, response.done
        # below fires it once that answer has fully finished.
        if not state.response_active and not absorbed:
            _schedule_reply(oai_ws, state)
        if (
            _agent_busy(state)
            and not _live_ready(state)
            and state.ack_stage is None
            and (state.ack_task is None or state.ack_task.done())
            and not (state.greeting_guard_until and time.monotonic() < state.greeting_guard_until)
        ):
            state.ack_task = asyncio.create_task(_ack_after_gap(oai_ws, call_ws, state, log))

    elif etype == "conversation.item.input_audio_transcription.completed":
        state.pending_user_transcript = (event.get("transcript") or "").strip()
        _trace(state, "transcript_arrived", ms_since_commit=_ms_since(state.t_committed), agent_busy=_agent_busy(state))
        if state.pending_user_transcript:
            _caller_is_active(state)
        state.transcripts_outstanding = max(0, state.transcripts_outstanding - 1)
        overlapping = state.interrupted_mid_response or state.restate_next or state.reply_pending
        if overlapping and _is_backchannel(state.pending_user_transcript):
            # Filler while the agent was talking ("ok", "theek hai"): not a
            # question. If nothing real is waiting, drop the pending reply so
            # it triggers no ack, read-back or new answer.
            log.info("voice_backchannel_ignored", text=state.pending_user_transcript)
            dropped = not state.overlap_transcripts and state.answer_after_restate is False
            if dropped:
                state.reply_pending = False
                state.restate_next = False
                state.interrupted_mid_response = False
            _trace(state, "backchannel", dropped=dropped, ms_since_commit=_ms_since(state.t_committed))
        elif state.pending_user_transcript and (state.interrupted_mid_response or state.restate_next):
            # Only a turn spoken over the agent is queued for the read-back. Before this, EVERY normal turn landed here
            # (a fresh commit always has reply_pending set), the list never emptied in the default mode, and one stale
            # entry made the backchannel drop below fail: "haan" was logged as ignored yet answered (Phase 8K live call).
            state.overlap_transcripts.append(state.pending_user_transcript)
        log.info("voice_user_transcript", text=state.pending_user_transcript)
        text = state.pending_user_transcript
        if text:
            state.tool_holds = {}  # a new thing was said: the previous turn's holds no longer apply
            state.caller_thinking = turn_controller.thinking_cue(text)
            if state.cut_at and time.monotonic() - state.cut_at < 6.0 and (
                _is_backchannel(text) or turn_controller.is_noise(text)
            ):
                # We stopped talking for something that was only an acknowledgement or noise.
                state.latency.event("false_interruption")
                state.cut_at = 0.0
        if text and settings.voice_end_call_enabled and dialogue.looks_like_end_request(text):
            # They asked to end it: let the agent say goodbye, then hang up. Not abortable.
            call_control.request_hangup(
                state, log, reason="customer_requested", outcome="customer_requested_end",
                after_response=True, abortable=False,
            )
        # Decision Layer: start deciding about this turn while the existing
        # answer gap elapses (filler like "ok" is not a turn worth deciding on).
        if text and not (overlapping and _is_backchannel(text)):
            _start_voice_decision(state, text)
        # Only switch language on an explicit request ("English mein baat
        # karo"). The old auto-hint (detect the caller's language from their
        # first transcript and switch to it unasked) used to flip the whole
        # call to English off a bare first "hello" — the agent now stays on
        # the Hindi greeting's language until the caller actually asks to
        # change it, never on a guess.
        if text and _LANGUAGE_SWITCH_CUE_RE.search(text):
            asyncio.create_task(_handle_language_switch_request(oai_ws, state, text, log))

    elif etype in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
        assistant_text = (event.get("transcript") or "").strip()
        # Muted response the caller never heard (see _cut_now) — don't log or
        # persist it as something the agent actually said.
        if assistant_text and not state.suppress_response_audio:
            state.last_spoken_text = assistant_text
            await _persist_voice_turn(state, assistant_text)
            log.info("voice_assistant_transcript", text=assistant_text)

    elif etype in ("response.text.delta", "response.output_text.delta"):
        # ElevenLabs path only — reached when session.update set
        # output_modalities=["text"], so OpenAI never emits audio deltas at
        # all. Opens the ElevenLabs session on the first delta and starts
        # forwarding its audio concurrently, rather than waiting for the
        # full response to finish.
        delta = event.get("delta") or ""
        state.resp_text += delta
        if state.suppress_response_audio:
            return
        if delta:
            _note_first_output(state, log, "text")
            state.latency.mark("openai_first_output")
        if state.tts_provider == "elevenlabs" and delta:
            if state.tts_failed_over:
                return  # the voice has failed over: nothing more is spoken on this call
            if state.tts_recovery_task is not None and not state.tts_recovery_task.done():
                state.eleven_text_buffer += delta  # recovery is replaying the turn; it picks this up
                return
            if _finalizing(state):
                # The previous turn's audio is still draining: never let two
                # turns share a session or interleave their audio.
                await asyncio.wait({state.eleven_finalize_task})
            if state.eleven_session is None:
                state.eleven_text_buffer = ""
                state.eleven_turn_audio_chunks = 0
                state.tts_turn_retried = False
                state.tts_text_done = False
                try:
                    session = await _acquire_tts_session(state, log)
                    if state.suppress_response_audio:
                        await session.__aexit__(None, None, None)
                        log.info("voice_tts_interrupted_open_dropped")
                        return
                    state.eleven_session = session
                except Exception as exc:  # noqa: BLE001
                    if not settings.voice_tts_recovery:
                        raise
                    state.eleven_text_buffer = delta
                    _spawn_tts_recovery(state, log, f"open_failed:{type(exc).__name__}")
                    return
                state.eleven_forward_task = asyncio.create_task(
                    _forward_elevenlabs_audio(call_ws, state, log)
                )
            if state.suppress_response_audio:
                return
            state.eleven_text_buffer += delta
            try:
                await state.eleven_session.send_text(delta)
            except Exception as exc:  # noqa: BLE001
                if state.suppress_response_audio:
                    log.info("voice_tts_interrupted_send_dropped")
                    return
                if not settings.voice_tts_recovery:
                    raise
                _spawn_tts_recovery(state, log, f"send_failed:{type(exc).__name__}")
                return
            state.tts_last_text_at = time.monotonic()
            _start_tts_watchdog(state, log)

    elif etype in ("response.text.done", "response.output_text.done"):
        if state.tts_provider == "elevenlabs" and state.eleven_session is not None:
            session = state.eleven_session
            assistant_text = (event.get("text") or state.eleven_text_buffer or "").strip()
            pending = state.pending_user_transcript
            state.tts_text_done = True  # kept so TTS recovery can replay and finish this turn
            state.tts_final_text = assistant_text
            state.tts_final_pending = pending
            if settings.voice_nonblocking_tts_finish:
                # Don't hold the OpenAI event loop while the last audio drains:
                # a caller speaking over the tail must be seen at once. The
                # task keeps the agent "playing" (_is_playing) until it ends.
                state.eleven_finalize_task = asyncio.create_task(
                    _finish_elevenlabs_turn(state, session, assistant_text, pending, log)
                )
            else:
                await _finish_elevenlabs_turn(state, session, assistant_text, pending, log)

    elif etype == "response.function_call_arguments.done":
        call_id = event.get("call_id")
        if call_id:
            if call_id in state.seen_tool_call_ids:
                log.info("voice_duplicate_tool_event_dropped")
                return
            # Reserve before dispatch yields: duplicate delivery must not
            # execute another side effect, even with recovery flags off.
            if len(state.seen_tool_call_ids) >= 256:
                log.warning("voice_tool_event_limit_reached")
                return
            state.seen_tool_call_ids.add(call_id)
        state.latency.note_tool_call()
        name = event.get("name") or ""
        args_json = event.get("arguments") or "{}"
        if name in _CALL_CONTROL_TOOLS:
            await _handle_call_control_tool(name, args_json, call_id, oai_ws, state, log)
            return
        result = await _dispatch_deduped(state, name, args_json, log)
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
        state.tool_response_pending = True
        log.info("voice_tool_dispatched", tool=name)

    elif etype == "response.done":
        done_id = (event.get("response") or {}).get("id")
        if done_id and done_id in state.completed_response_ids:
            log.info("voice_duplicate_response_done_dropped")
            return
        if done_id and state.response_id and done_id != state.response_id:
            log.info("voice_stale_response_done_dropped")
            return
        if done_id:
            if len(state.completed_response_ids) >= 256:
                state.completed_response_ids.pop()
            state.completed_response_ids.add(done_id)
        if done_id and done_id in state.trim_pending:
            await _trim_context(oai_ws, state, log, done_id)
        if state.cancel_sent_at and (done_id is None or done_id in state.cancelled_response_ids):
            state.latency.cancel_completed(
                round((time.monotonic() - state.cancel_sent_at) * 1000), response_id=done_id
            )
            _clear_cancel_outstanding(state)
        state.response_active = False
        state.suppress_response_audio = False
        state.responses_done += 1
        try:
            state.usage.add_response(event.get("response"))
        except Exception:  # noqa: BLE001 - a bad usage report must never disturb response bookkeeping
            log.warning("voice_usage_report_unreadable", exc_info=True)
        state.latency.response_done(max(0.0, state.playback_end - time.monotonic()))
        if state.eleven_prewarm_task is not None and state.eleven_session is None:
            # Prewarmed for a response that never spoke (a tool call): don't
            # leave an idle socket open against the provider.
            await _discard_prewarm(state)
        # The opening greeting has finished playing — lift the barge-in
        # suppression so normal interruption works for the rest of the call.
        # (its audio may still be queued at the provider, so hold the guard
        # until that playback ends.)
        if state.greeting_guard_until and not state.greeting_guard_extended:
            # Only the greeting's own response.done: extend the guard once, to
            # when the greeting audio stops. Later responses must not renew it
            # or every answer would suppress the caller's speech.
            state.greeting_guard_extended = True
            if _finalizing(state):
                # playback_end only covers audio already handed to the provider.
                await asyncio.wait({state.eleven_finalize_task})
            state.greeting_guard_until = state.playback_end
        else:
            state.greeting_guard_until = 0.0
        if state.tool_goodbye_pending:
            state.tool_goodbye_pending = False
            state.tool_response_pending = False
            state.response_active = True
            await oai_ws.send(_oob_response(
                _GOODBYE_REQUEST, f"Language sample of the call so far: {_language_sample(state)!r}"
            ))
            return
        if state.tool_response_pending:
            state.tool_response_pending = False
            # Coalesce parallel tool outputs into one continuation after the
            # function-calling response has actually completed.
            if state.ack_stage is None:
                state.reply_pending = True
                await _fire_pending_reply(oai_ws, state)
                return
        if state.ack_stage == "cancelling":
            if _answer_immediately():
                # The cut-off answer's response.done — answer the question now.
                state.ack_stage = None
                if state.reply_pending:
                    await _fire_pending_reply(oai_ws, state)
                return
            # The paused answer's response.done — play the short ack now.
            await _send_ack(oai_ws, state)
            return
        if state.ack_stage == "ack":
            if state.paused_text:
                await _send_resume(oai_ws, state)
                return
            # Nothing to finish (e.g. the paused answer produced no text): go
            # straight on to the read-back and answer.
            state.ack_stage = None
        if state.ack_stage == "resume":
            state.ack_stage = None
            # Consumed — otherwise this stays set for the rest of the call
            # and _language_sample keeps preferring it over whatever the
            # agent is actually saying now (the original bug: one early
            # interruption's frozen text would silently outrank every later
            # ack/restate beat's language signal).
            state.paused_text = ""
        if state.answer_after_restate:
            state.answer_after_restate = False
            # Restate beat is done. If the caller spoke again meanwhile, the
            # pending reply restates that newer question instead.
            if not state.reply_pending:
                asyncio.create_task(_answer_after_readback(oai_ws, state))
                return
        if state.reply_pending:
            _schedule_reply(oai_ws, state)
        else:
            _arm_silence(oai_ws, state, log)

    elif etype == "error":
        log.warning("openai_realtime_error", error_code=(event.get("error") or {}).get("code"))
        err = event.get("error") or {}
        if (
            state.cancel_sent_at
            and "cancel_not_active" not in str(err.get("code"))
            and time.monotonic() - state.cancel_sent_at < 5.0
        ):
            # An error right after our cancel: don't trust cancel again on this call.
            _trip_cancel_breaker(state, log, f"error_after_cancel:{err.get('code')}")
        if state.ack_stage == "cancelling" and "cancel_not_active" in str(err.get("code")):
            _clear_cancel_outstanding(state)
            # The answer finished on its own just before our cancel landed;
            # no ack needed, carry on with the normal pending reply.
            state.ack_stage = None
            state.response_active = False
            if state.reply_pending:
                _schedule_reply(oai_ws, state)

    elif etype not in ("session.created", "session.updated"):
        log.info("voice_openai_event_unhandled", etype=etype)
