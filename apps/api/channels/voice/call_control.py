"""Ending a call on purpose, safely.

Why this exists: the Plivo answer markup uses ``keepCallAlive="true"`` so a dropped
media stream does not cut a caller off mid-sentence. The flip side is that closing
OUR side never hangs up: the only way to end a call is the provider's Call API.
Before this module nothing ended a call on purpose (the agent could say goodbye and
then the line stayed open), and when the Realtime session could not be recovered the
caller was left on an open, silent line.

``request_hangup`` is the single entry point (the ``end_call`` tool, a caller's
explicit request, a silence give-up, and the failure paths all use it). It is:

* **Idempotent** - a second request while one is pending, or after the call is already
  over, is a no-op that returns the existing task. The provider is asked at most once
  to hang up per success; "already ended" is treated as success, never as an error.
* **Patient but bounded** - it waits for the response in flight and for the audio
  already queued to finish playing (so the goodbye is not cut off), but never longer
  than ``VOICE_END_CALL_MAX_WAIT_SECONDS`` per stage; every number is clamped so a bad
  setting cannot remove the bound.
* **Abortable until it commits** - an agent-initiated hangup is cancelled if the caller
  starts talking again; a caller-requested one is not.
* **Provider-independent** - Plivo and Twilio each use their own client and the org's own
  credentials.
* **Self-cleaning** - it records the call outcome on the conversation, and once the
  provider confirms the call is over it closes the stream socket so the bridge winds
  down immediately.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog

from apps.api.channels.voice import plivo_client, twilio_client
from apps.api.config import settings
from apps.api.core.org_credentials import resolve_plivo_credentials, resolve_twilio_credentials
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# Outcomes stored on the conversation as an "outcome:<name>" tag.
OUTCOMES = (
    "completed",
    "customer_requested_end",
    "no_response",
    "tts_failure",
    "realtime_unavailable",
    "error",
)
_POLL_SECONDS = 0.1
_HANGUP_ATTEMPTS = 2
# A hangup the provider would not confirm is tried again a couple of times, further apart, before it is
# reported as one that may have left the line open.
_HANGUP_ROUNDS = 3
_HANGUP_ROUND_GAP_SECONDS = 3.0


def bounded(value: Any, lo: float, hi: float, default: float) -> float:
    """``value`` as a float within [lo, hi]; ``default`` if it is not a usable number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))


def _playing(state: Any) -> bool:
    return state.playback_end > time.monotonic() or (
        state.eleven_finalize_task is not None and not state.eleven_finalize_task.done()
    )


async def _wait_for(condition: Any, timeout: float) -> bool:
    """Poll ``condition()`` until it is true or ``timeout`` passes; True if it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(_POLL_SECONDS)
    return bool(condition())


async def _load_org(org_id: UUID) -> Org | None:
    async with AsyncSessionLocal() as db:
        return await db.get(Org, org_id)


async def provider_hangup(state: Any, log: Any) -> str:
    """Ask the call's own provider (with the org's own credentials) to end it.
    ``"ok"``, ``"already_ended"``, ``"failed"`` or ``"no_credentials"``. Never raises."""
    try:
        org = await _load_org(state.org_id)
        if state.provider == "twilio":
            creds, client = resolve_twilio_credentials(org), twilio_client
        else:
            creds, client = resolve_plivo_credentials(org), plivo_client
        if not client.is_configured(creds) or not state.call_uuid:
            return "no_credentials"
        result = "failed"
        for attempt in range(_HANGUP_ATTEMPTS):
            result = await client.hangup_call(creds, state.call_uuid)
            if result != "failed":
                return result
            await asyncio.sleep(0.5 * (attempt + 1))
        return result
    except Exception:  # noqa: BLE001 - ending a call must never raise into the caller
        log.warning("voice_hangup_error", exc_info=True)
        return "failed"


async def record_call_outcome(conversation_id: UUID | None, org_id: UUID, outcome: str) -> None:
    """Tag the conversation ``outcome:<name>`` (one outcome at a time, org-checked).
    Best-effort."""
    if conversation_id is None:
        return
    try:
        async with AsyncSessionLocal() as db:
            conversation = await db.get(Conversation, conversation_id)
            if conversation is None or conversation.org_id != org_id:
                return
            tags = [t for t in (conversation.tags or []) if not str(t).startswith("outcome:")]
            conversation.tags = [*tags, f"outcome:{outcome}"]
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("voice_call_outcome_failed", exc_info=True)


async def _close_stream(state: Any) -> None:
    ws = getattr(state, "call_ws", None)
    if ws is None:
        return
    try:
        await ws.close()
    except Exception:  # noqa: BLE001 - already closed is fine
        pass


async def _hangup_worker(state: Any, log: Any, reason: str, outcome: str, after_response: bool) -> None:
    max_wait = bounded(settings.voice_end_call_max_wait_seconds, 0.5, 30.0, 8.0)
    grace = bounded(settings.voice_end_call_grace_seconds, 0.0, 5.0, 0.8)
    try:
        if after_response:
            # The caller just asked to end: let the agent say its goodbye first.
            await _wait_for(
                lambda: state.responses_done > state.responses_done_at_request or state.call_ended, max_wait
            )
        # Essential speech: the response in flight, then whatever audio is still queued.
        await _wait_for(lambda: state.call_ended or not (state.response_active or _playing(state)), max_wait)
        if not state.call_ended and grace:
            await asyncio.sleep(grace)  # the carrier buffers a little beyond what we have sent
        state.hangup_committed = True  # past this point a caller speaking can no longer cancel it

        started = time.monotonic()
        result = "already_ended" if state.call_ended else await provider_hangup(state, log)
        rounds = 1
        while result in ("failed", "no_credentials") and rounds < _HANGUP_ROUNDS and not state.call_ended:
            if result == "no_credentials":
                break  # retrying cannot create credentials
            await asyncio.sleep(_HANGUP_ROUND_GAP_SECONDS)
            result = await provider_hangup(state, log)
            rounds += 1
        if result in ("failed", "no_credentials") and not state.call_ended:
            log.error("voice_hangup_failed_line_may_be_open", result=result, rounds=rounds)
        state.latency.metric("hangup_ms", (time.monotonic() - started) * 1000, result=result, rounds=rounds)
        log.info("voice_call_hangup", reason=reason, outcome=outcome, result=result)
        if result in ("ok", "already_ended"):
            state.hangup_done = True
        else:
            outcome = "error" if outcome == "completed" else outcome
            log.warning("voice_hangup_not_confirmed", result=result)
        await record_call_outcome(state.conversation_id, state.org_id, outcome)
        if state.hangup_done:
            await _close_stream(state)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.warning("voice_hangup_worker_failed", exc_info=True)


def request_hangup(
    state: Any,
    log: Any,
    *,
    reason: str,
    outcome: str = "completed",
    after_response: bool = False,
    abortable: bool = True,
) -> "asyncio.Task[None] | None":
    """Ask for the call to end once the essential speech has played. Idempotent: returns the
    pending task if one exists, and does nothing if the call is already over."""
    if state.hangup_done:
        log.info("voice_hangup_duplicate_ignored", reason=reason, why="already_done")
        return state.hangup_task
    if state.hangup_task is not None and not state.hangup_task.done():
        log.info("voice_hangup_duplicate_ignored", reason=reason, why="pending")
        return state.hangup_task
    state.hangup_reason = reason
    state.hangup_abortable = abortable
    state.hangup_committed = False
    # A response already in flight is not the goodbye the caller's request will produce.
    state.responses_done_at_request = state.responses_done + (1 if after_response and state.response_active else 0)
    state.hangup_task = asyncio.create_task(_hangup_worker(state, log, reason, outcome, after_response))
    return state.hangup_task


def abort_hangup(state: Any, log: Any) -> bool:
    """The caller started talking again before an agent-initiated hangup committed:
    cancel it. Returns True if one was cancelled."""
    task = state.hangup_task
    if task is None or task.done() or state.hangup_committed or not state.hangup_abortable:
        return False
    task.cancel()
    state.hangup_task = None
    log.info("voice_hangup_aborted_caller_spoke", reason=state.hangup_reason)
    return True


async def start_call_recording(state: Any, log: Any) -> bool:
    """Start the provider's recording for this call (used once a caller has consented). Uses the org's
    own credentials. True if the request was accepted. The provider's start_recording never raises."""
    try:
        org = await _load_org(state.org_id)
        callback = f"{settings.public_base_url.rstrip('/')}/voice/recording-callback"
        if state.provider == "twilio":
            creds, client = resolve_twilio_credentials(org), twilio_client
        else:
            creds, client = resolve_plivo_credentials(org), plivo_client
        if not client.is_configured(creds) or not state.call_uuid:
            return False
        await client.start_recording(creds, state.call_uuid, callback)
        return True
    except Exception:  # noqa: BLE001
        log.warning("voice_start_recording_failed", exc_info=True)
        return False


def cancel_pending_hangup(state: Any) -> None:
    """The bridge is tearing down because the stream ended: a hangup still waiting has nothing left to
    do (the caller is gone). One that already committed to the provider is left to finish."""
    task = state.hangup_task
    if task is not None and not task.done() and not state.hangup_committed:
        task.cancel()
