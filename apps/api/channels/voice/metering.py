"""Measured provider usage for a voice call, recorded through the existing usage ledger.

Everything recorded here is a *measurement of what was sent to or reported by a
provider*, labelled ``basis: measured`` in the event metadata. No prices are
applied and nothing is estimated: turning usage into cost is the job of the
existing ``CostRate`` table (admin-set, per service/usage type/provider), which
this does not touch. If a provider does not report a number (or the field is not
in the event we receive) nothing is recorded for it.

What is measured, and how double counting is avoided:

* **Realtime tokens** - read from each ``response.done`` event's ``usage``. A response
  id is counted once, so a duplicated event, a reconnect or a cancelled-then-finished
  response cannot be counted twice. A response the caller interrupted *is* counted:
  the provider bills the tokens it generated.
* **Live transcription seconds** - the audio actually streamed to the transcription
  connection (bytes at 8 kHz mu-law), not the call length.
* **TTS characters** - characters actually sent to ElevenLabs on its sockets, with the
  characters re-sent by a recovery retry shown separately in the metadata.
* **Decision-layer / turn-judge tokens** - from the chat-completions results.
* **Call seconds** - the existing ``voice_seconds`` event (unchanged id, so a retry of the
  call-end handler still records it once).

Events are written once, at call end, with ids derived from the provider's call id.
A process crash mid-call therefore loses that call's token/character events (the
call-seconds event has the same property): they are not reconstructed or estimated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.usage import deterministic_event_id, record_usage

logger = structlog.get_logger(__name__)

_BYTES_PER_SECOND = 8000  # mu-law, 8 kHz, one byte per sample


@dataclass
class CallUsage:
    """Accumulated per call, on ``CallState``. Plain counters, no I/O."""

    rt_input_text: int = 0
    rt_input_audio: int = 0
    rt_input_cached: int = 0
    rt_output_text: int = 0
    rt_output_audio: int = 0
    counted_responses: set[str] = field(default_factory=set)
    responses_without_usage: int = 0
    transcribe_bytes: int = 0
    tts_chars: int = 0
    tts_retry_chars: int = 0
    judge_in: int = 0
    judge_out: int = 0
    reconnects: int = 0
    # Checkpointing: what has already been written to the ledger, and the checkpoint number. The ledger
    # receives DELTAS (quantity since the last confirmed write), each under a deterministic id that
    # includes the checkpoint number, so writes are idempotent and a crash keeps everything up to the
    # last checkpoint. Checkpoint 0 keeps the original ids.
    flushed: dict[str, float] = field(default_factory=dict)
    seq: int = 0
    _pending: dict[str, float] = field(default_factory=dict, repr=False)

    def add_response(self, response: dict[str, Any] | None) -> bool:
        """Count one ``response.done`` payload. False if it has no usable usage block or was
        already counted."""
        if not isinstance(response, dict):
            return False
        usage = response.get("usage")
        response_id = response.get("id")
        if not isinstance(usage, dict) or not usage:
            self.responses_without_usage += 1
            return False
        if isinstance(response_id, str):
            if response_id in self.counted_responses:
                return False
            self.counted_responses.add(response_id)
        in_details = usage.get("input_token_details")
        out_details = usage.get("output_token_details")
        in_details = in_details if isinstance(in_details, dict) else {}
        out_details = out_details if isinstance(out_details, dict) else {}
        total_in = _int(usage.get("input_tokens"))
        total_out = _int(usage.get("output_tokens"))
        audio_in = _int(in_details.get("audio_tokens"))
        cached = _int(in_details.get("cached_tokens"))
        audio_out = _int(out_details.get("audio_tokens"))
        self.rt_input_audio += audio_in
        self.rt_input_cached += cached
        self.rt_input_text += max(0, total_in - audio_in)
        self.rt_output_audio += audio_out
        self.rt_output_text += max(0, total_out - audio_out)
        return True

    def add_tts_chars(self, count: int, *, retry: bool = False) -> None:
        if count > 0:
            if retry:
                self.tts_retry_chars += count
            else:
                self.tts_chars += count

    def add_judge(self, tokens_in: int, tokens_out: int) -> None:
        self.judge_in += max(0, tokens_in)
        self.judge_out += max(0, tokens_out)

    def totals(self, call_seconds: float | None = None) -> dict[str, float]:
        """Cumulative measured quantities per ledger kind, as of now."""
        totals: dict[str, float] = {
            "rt_in": self.realtime_input_tokens,
            "rt_out": self.realtime_output_tokens,
            "transcribe": self.transcribe_seconds,
            "tts": self.tts_chars + self.tts_retry_chars,
            "tts_retry": self.tts_retry_chars,
            "judge_in": self.judge_in,
            "judge_out": self.judge_out,
        }
        if call_seconds is not None:
            totals["call_seconds"] = round(max(0.0, call_seconds), 1)
        return totals

    def confirm(self) -> None:
        """The last ``flush_call_usage`` was committed: what it covered is now in the ledger."""
        if self._pending:
            self.flushed = dict(self._pending)
            self.seq += 1
            self._pending = {}

    @property
    def transcribe_seconds(self) -> float:
        return round(self.transcribe_bytes / _BYTES_PER_SECOND, 2)

    @property
    def realtime_input_tokens(self) -> int:
        return self.rt_input_text + self.rt_input_audio

    @property
    def realtime_output_tokens(self) -> int:
        return self.rt_output_text + self.rt_output_audio


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


MEASURED = {"basis": "measured"}


async def flush_call_usage(
    db: AsyncSession,
    *,
    org_id: UUID,
    call_uuid: str,
    provider: str,
    usage: CallUsage,
    call_seconds: float | None = None,
) -> list[str]:
    """Write what the call has used since the last confirmed write (idempotent per call id and checkpoint).
    Returns the usage types written. Caller commits, then calls ``usage.confirm()``. A no-op unless
    ``VOICE_METERING_V2`` is on. ``call_seconds`` (the call's duration so far) also writes the
    ``voice_seconds`` delta; leave it None to leave call duration to the legacy end-of-call event."""
    if not settings.voice_metering_v2 or not call_uuid:
        return []
    written: list[str] = []
    totals = usage.totals(call_seconds)
    delta = {kind: totals[kind] - usage.flushed.get(kind, 0) for kind in totals}
    usage._pending = totals

    async def emit(kind: str, usage_type: str, unit: str, prov: str, source: str, extra: dict[str, Any],
                   service: str = "ai") -> None:
        quantity = delta.get(kind, 0)
        if quantity <= 0:
            return
        parts = ["voice_usage", kind, call_uuid] + ([str(usage.seq)] if usage.seq else [])
        event = await record_usage(
            db,
            organization_id=org_id,
            event_id=deterministic_event_id(*parts),
            service=service,
            usage_type=usage_type,
            quantity=quantity,
            unit=unit,
            provider=prov,
            source=source,
            request_id=call_uuid,
            metadata={**MEASURED, "checkpoint": usage.seq, **extra},
        )
        if event is not None:
            written.append(usage_type)

    rt_meta = {
        "model": settings.openai_realtime_model,
        # Breakdowns are cumulative to this checkpoint; the event quantity is the delta.
        "breakdown_is_cumulative": True,
        "audio_tokens": usage.rt_input_audio,
        "text_tokens": usage.rt_input_text,
        "cached_tokens": usage.rt_input_cached,
        "responses_counted": len(usage.counted_responses),
        "responses_without_usage": usage.responses_without_usage,
    }
    await emit("rt_in", "ai_input_tokens", "tokens", "openai", "voice_realtime", rt_meta)
    await emit("rt_out", "ai_output_tokens", "tokens", "openai", "voice_realtime",
               {**rt_meta, "audio_tokens": usage.rt_output_audio, "text_tokens": usage.rt_output_text})
    await emit("transcribe", "transcribe_seconds", "seconds", "openai", "voice_live_transcribe",
               {"model": settings.voice_live_transcribe_model, "what": "audio streamed to the transcription connection"})
    await emit("tts", "tts_characters", "characters", "elevenlabs", "voice_tts",
               {"retry_characters": delta.get("tts_retry", 0), "model": settings.elevenlabs_model_id})
    await emit("judge_in", "ai_input_tokens", "tokens", "openai", "voice_turn_judge", {})
    await emit("judge_out", "ai_output_tokens", "tokens", "openai", "voice_turn_judge", {})
    await emit("call_seconds", "voice_seconds", "seconds", provider, "realtime_bridge", {}, service="voice")
    return written


async def checkpoint_call_usage(
    session_factory: Any,
    *,
    org_id: UUID,
    call_uuid: str,
    provider: str,
    usage: CallUsage,
    call_seconds: float | None,
) -> bool:
    """Write a checkpoint in its own transaction. True if committed. A failure is logged and leaves the
    call's accounting where it was, so the next checkpoint simply covers more; it never raises."""
    try:
        async with session_factory() as db:
            await flush_call_usage(
                db, org_id=org_id, call_uuid=call_uuid, provider=provider, usage=usage, call_seconds=call_seconds
            )
            await db.commit()
        usage.confirm()
        return True
    except Exception:  # noqa: BLE001 - metering must never affect a call
        logger.warning("voice_usage_checkpoint_failed", call_uuid=call_uuid, exc_info=True)
        usage._pending = {}
        return False
