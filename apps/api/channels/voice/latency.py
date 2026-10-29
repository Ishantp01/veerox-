"""Per-turn latency instrumentation for the voice pipeline.

Observational only: nothing here changes what the caller hears. One
``LatencyTracker`` lives on each call's ``CallState``; the adapter / bridge /
TTS forwarder drop ``mark()`` calls at the points listed in ``TURN_STAGES`` and
the tracker emits a single structured ``voice_turn_latency`` log line per turn
(plus ``voice_turn_playback`` and ``voice_interruption_latency``). It never
logs audio, transcripts or the caller's phone number — only ids, stage names
and millisecond offsets.

"First audio sent" is the moment the first audio frame was handed to the
telephony provider's WebSocket. It is NOT when the caller hears it: network
transit, the provider's jitter buffer and the handset add time we cannot
observe from here. Treat ``first_audio_sent`` as a lower bound on what the
caller experienced. Likewise ``est_playback_ms`` is derived from the byte
count we sent (mu-law 8 kHz), not from a provider acknowledgement.

A rolling in-process aggregate (``STATS``) backs ``GET /diag/voice-latency``;
for a fleet-wide / historical view run ``scripts/voice_latency_report.py`` over
the JSON logs. Percentiles use the nearest-rank method and are only meaningful
once ``n`` is large — the output always carries ``n`` so a thin sample can't be
mistaken for a representative one.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

# Canonical order of the stages inside one reply turn. Not every turn hits
# every stage (the native-OpenAI voice path has no ``tts_*`` stages; a tool
# call round adds time inside ``request_to_model_output``).
TURN_STAGES: tuple[str, ...] = (
    "speech_stopped",  # provider VAD: caller stopped talking
    "committed",  # OpenAI committed the caller's audio turn
    "transcript_final",  # the final (Whisper) transcript of the turn arrived; usually AFTER the reply started
    "response_requested",  # we sent response.create
    "openai_response_created",  # OpenAI acknowledged the response
    "openai_first_output",  # first text delta (ElevenLabs path) / audio delta (native)
    "tts_connect_started",  # began opening the ElevenLabs socket (possibly prewarmed)
    "tts_ready",  # ElevenLabs socket open and handshaken
    "tts_first_chunk",  # first audio chunk back from ElevenLabs
    "first_audio_sent",  # first audio frame handed to Plivo/Twilio
)

_STATS_WINDOW = 1000


def percentile(values: Iterable[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for an empty sample."""
    data = sorted(values)
    if not data:
        return None
    rank = max(1, math.ceil(q / 100.0 * len(data)))
    return data[min(rank, len(data)) - 1]


def summarize(values: Iterable[float]) -> dict[str, Any]:
    data = list(values)
    return {
        "n": len(data),
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "p99": percentile(data, 99),
    }


def _ms(marks: Mapping[str, float], a: str, b: str) -> int | None:
    if a in marks and b in marks:
        return max(0, round((marks[b] - marks[a]) * 1000))
    return None


def compute_segments(marks: Mapping[str, float]) -> dict[str, int]:
    """Split one turn's marks into named, non-overlapping-ish segments (ms).

    ``total`` is caller-stopped-speaking -> first audio sent (or, for turns
    with no ``speech_stopped`` such as the greeting, request -> first audio).
    ``request_to_first_audio`` isolates the pipeline itself from our own reply
    gating (endpoint hold, waiting for earlier audio to drain).
    """
    seg: dict[str, int | None] = {}
    seg["vad_to_commit"] = _ms(marks, "speech_stopped", "committed")
    commit_from = "committed" if "committed" in marks else "speech_stopped"
    seg["commit_to_transcript"] = _ms(marks, "committed", "transcript_final")
    seg["commit_to_request"] = _ms(marks, commit_from, "response_requested")
    seg["request_to_model_output"] = _ms(marks, "response_requested", "openai_first_output")
    if "tts_ready" in marks and "openai_first_output" in marks:
        # Connect time NOT hidden behind the model's own first-token time: a
        # prewarmed socket that was ready before the first token costs 0 here.
        seg["tts_connect_wait"] = max(
            0, round((marks["tts_ready"] - marks["openai_first_output"]) * 1000)
        )
        ready_at = max(marks["tts_ready"], marks["openai_first_output"])
        if "tts_first_chunk" in marks:
            seg["tts_first_chunk"] = max(0, round((marks["tts_first_chunk"] - ready_at) * 1000))
    seg["chunk_to_provider"] = _ms(marks, "tts_first_chunk", "first_audio_sent")
    if "tts_first_chunk" not in marks:
        # Native OpenAI voice: audio goes straight through.
        seg["model_output_to_audio_sent"] = _ms(marks, "openai_first_output", "first_audio_sent")
    seg["request_to_first_audio"] = _ms(marks, "response_requested", "first_audio_sent")
    start = next((s for s in ("speech_stopped", "response_requested") if s in marks), None)
    seg["total"] = _ms(marks, start, "first_audio_sent") if start else None
    return {k: v for k, v in seg.items() if v is not None}


class RollingStats:
    """Bounded in-process sample store, one deque per metric name."""

    def __init__(self, window: int = _STATS_WINDOW) -> None:
        self._window = window
        self._series: dict[str, deque[float]] = {}

    def add(self, name: str, value: float) -> None:
        self._series.setdefault(name, deque(maxlen=self._window)).append(value)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: summarize(vals) for name, vals in sorted(self._series.items())}

    def reset(self) -> None:
        self._series.clear()


STATS = RollingStats()


# --- call start-up timeline ---------------------------------------------------------------------------------------------------------
#
# What happens between the callee answering and the first greeting audio is spread across the answer webhook, the media
# WebSocket and the bridge, so no single tracker sees it. Each side drops a named mark here, keyed by the provider call id;
# when the first audio frame is sent the marks are logged once as ``voice_startup_timeline`` (milliseconds since the first
# mark, i.e. since our answer handler began) and forgotten. Process-local (the answer webhook and the stream of one call
# land in the same process), bounded, and incapable of raising. Ids and stage names only.

STARTUP_STAGES = (
    "answer_received",      # the answer webhook began (the nearest thing we can see to "Plivo answered")
    "ticket_checked",       # signature and single-use ticket accepted
    "answer_served",        # the XML response was ready to return
    "stream_accepted",      # the media WebSocket handshake began
    "handshake_done",       # the provider's connected/start frames were read
    "precall_claimed",      # the pre-connected OpenAI session was taken (or found missing)
    "context_loaded",       # org greeting, voice and recording policy were read
    "conversation_opened",  # the user, lead and conversation rows exist
    "greeting_started",     # the greeting was requested (spoken) or sent (cached)
    "first_audio_sent",     # the first audio frame went to the provider
)
_STARTUPS: "OrderedDict[str, dict[str, float]]" = OrderedDict()
_STARTUP_MAX = 32


def startup_mark(call_id: str | None, stage: str, *, at: float | None = None) -> None:
    """Record ``stage`` for this call (first mark of a stage wins). No-op without a call id or when tracking is off."""
    if not call_id or not settings.voice_latency_tracking:
        return
    try:
        marks = _STARTUPS.get(call_id)
        if marks is None:
            marks = _STARTUPS[call_id] = {}
            while len(_STARTUPS) > _STARTUP_MAX:
                _STARTUPS.popitem(last=False)
        marks.setdefault(stage, time.monotonic() if at is None else at)
    except Exception:  # noqa: BLE001 - instrumentation must never touch the call
        return


def startup_snapshot(call_id: str | None) -> dict[str, int]:
    """Stage -> milliseconds since the earliest mark, in stage order."""
    marks = _STARTUPS.get(call_id or "") or {}
    if not marks:
        return {}
    base = min(marks.values())
    return {stage: round((marks[stage] - base) * 1000) for stage in STARTUP_STAGES if stage in marks}


def startup_emit(call_id: str | None, **extra: Any) -> None:
    """Log the timeline once (``voice_startup_timeline``) and forget the call."""
    if not call_id:
        return
    try:
        snapshot = startup_snapshot(call_id)
        _STARTUPS.pop(call_id, None)
        if snapshot:
            logger.info("voice_startup_timeline", call_uuid=call_id, **extra, **{f"{k}_ms": v for k, v in snapshot.items()})
    except Exception:  # noqa: BLE001
        return


@dataclass
class _Turn:
    seq: int
    kind: str
    marks: dict[str, float] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    emitted: bool = False
    playback_logged: bool = False


class LatencyTracker:
    """Stage clock for one call. Every method is a cheap no-op when disabled
    and never raises — instrumentation must not be able to hurt a call."""

    def __init__(
        self,
        *,
        org_id: UUID | str | None = None,
        call_uuid: str | None = None,
        provider: str | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = settings.voice_latency_tracking if enabled is None else enabled
        self._clock = clock
        self._ids: dict[str, Any] = {
            "org_id": str(org_id) if org_id else None,
            "call_uuid": call_uuid or None,
            "provider": provider,
        }
        self._turn: _Turn | None = None
        self._seq = 0
        self._intr: dict[str, float] = {}

    # -- turn lifecycle ----------------------------------------------------------

    def begin_turn(self, kind: str = "reply") -> None:
        """Start timing a new turn, discarding one that never produced audio
        (a dropped filler, a turn answered by a tool only)."""
        if not self.enabled:
            return
        self._seq += 1
        self._turn = _Turn(seq=self._seq, kind=kind)

    def mark(self, stage: str, *, first_only: bool = True) -> None:
        if not self.enabled:
            return
        turn = self._turn
        if turn is None or turn.emitted:
            return
        if first_only and stage in turn.marks:
            return
        turn.marks[stage] = self._clock()

    def note(self, **extra: Any) -> None:
        """Attach a small scalar fact to the open turn (``agent_was_busy``...)."""
        turn = self._turn
        if self.enabled and turn is not None and not turn.emitted:
            turn.extra.update(extra)

    def note_tool_call(self) -> None:
        turn = self._turn
        if self.enabled and turn is not None and not turn.emitted:
            turn.extra["tool_calls"] = int(turn.extra.get("tool_calls", 0)) + 1

    def mark_first_audio_sent(self) -> None:
        """Called for every audio frame handed to the provider; only the first
        one of an open turn counts and triggers the log line."""
        if not self.enabled:
            return
        turn = self._turn
        if turn is None or turn.emitted:
            return
        self.mark("first_audio_sent")
        turn.emitted = True
        if turn.kind in ("greeting", "greeting_cached"):
            startup_mark(self._ids.get("call_uuid"), "first_audio_sent")
            startup_emit(self._ids.get("call_uuid"), kind=turn.kind)
        try:
            self._emit_turn(turn)
        except Exception:  # noqa: BLE001 — instrumentation must never touch the call
            logger.warning("voice_latency_emit_failed", exc_info=True)

    def response_done(self, playback_remaining_s: float) -> None:
        """The agent's response finished generating. Logs the estimated time
        the already-sent audio still has to play (estimate, see module doc)."""
        turn = self._turn
        if not self.enabled or turn is None or not turn.emitted or turn.playback_logged:
            return
        turn.playback_logged = True
        first_audio = turn.marks.get("first_audio_sent")
        if first_audio is None:
            return
        now = self._clock()
        est_playback_ms = max(0, round((now + max(0.0, playback_remaining_s) - first_audio) * 1000))
        logger.info(
            "voice_turn_playback",
            **self._ids,
            turn=turn.seq,
            kind=turn.kind,
            est_playback_ms=est_playback_ms,
            note="estimated from bytes sent; not a provider acknowledgement",
        )
        STATS.add("est_playback_ms", est_playback_ms)

    # -- interruptions ---------------------------------------------------------------

    def interruption_detected(self) -> None:
        """The caller started speaking while the agent was talking."""
        if self.enabled and "detected" not in self._intr:
            self._intr["detected"] = self._clock()

    def interruption_cut_decided(self) -> None:
        if self.enabled and "detected" in self._intr and "cut_decided" not in self._intr:
            self._intr["cut_decided"] = self._clock()

    def interruption_cleared(self) -> None:
        """Queued playback was dropped at the provider."""
        if self.enabled and "detected" in self._intr and "cleared" not in self._intr:
            self._intr["cleared"] = self._clock()

    def interruption_next_response(self) -> None:
        """A new response started after a cleared interruption — closes the
        episode and logs it."""
        intr = self._intr
        if not self.enabled or "cleared" not in intr:
            return
        self._intr = {}
        now = self._clock()
        data = {
            "detected_to_cleared_ms": round((intr["cleared"] - intr["detected"]) * 1000),
            "cleared_to_next_response_ms": round((now - intr["cleared"]) * 1000),
            "detected_to_next_response_ms": round((now - intr["detected"]) * 1000),
        }
        if "cut_decided" in intr:
            data["detected_to_cut_decision_ms"] = round((intr["cut_decided"] - intr["detected"]) * 1000)
        logger.info("voice_interruption_latency", **self._ids, **data)
        for name, value in data.items():
            STATS.add(name, value)

    def metric(self, name: str, value_ms: float, **extra: Any) -> None:
        """One named duration (``tts_connect_ms``, ``reconnect_recovery_ms``, ``hangup_ms``...). Logged as
        ``voice_metric`` and added to the rolling aggregate. Ids only, never content."""
        if not self.enabled:
            return
        try:
            logger.info("voice_metric", **self._ids, metric=name, value_ms=round(float(value_ms)), **extra)
            STATS.add(name, float(value_ms))
        except Exception:  # noqa: BLE001 - instrumentation must never touch the call
            logger.warning("voice_metric_failed", exc_info=True)

    def event(self, kind: str, **extra: Any) -> None:
        """A countable turn-taking occurrence (``false_interruption``, ``premature_response_suspected``,
        ``stale_audio_dropped``, ``silence_prompt``...). Logged as ``voice_turn_event``; counts and rates per
        100 turns come from ``summarize_events``. Ids only, never content."""
        if not self.enabled:
            return
        try:
            logger.info("voice_turn_event", **self._ids, kind=kind, **extra)
        except Exception:  # noqa: BLE001 - instrumentation must never touch the call
            logger.warning("voice_turn_event_failed", exc_info=True)

    def cancel_completed(self, elapsed_ms: int, *, response_id: str | None = None) -> None:
        """A response.cancel took ``elapsed_ms`` to be confirmed by response.done."""
        if not self.enabled:
            return
        logger.info("voice_cancel_latency", **self._ids, response_id=response_id, cancel_to_done_ms=elapsed_ms)
        STATS.add("cancel_to_done_ms", elapsed_ms)

    # -- internals ---------------------------------------------------------------

    def _emit_turn(self, turn: _Turn) -> None:
        marks = turn.marks
        origin = min(marks.values())
        marks_ms = {s: round((marks[s] - origin) * 1000) for s in TURN_STAGES if s in marks}
        segments = compute_segments(marks)
        logger.info(
            "voice_turn_latency",
            **self._ids,
            turn=turn.seq,
            kind=turn.kind,
            total_ms=segments.get("total"),
            segments_ms=segments,
            marks_ms=marks_ms,
            **turn.extra,
        )
        for name, value in segments.items():
            STATS.add(name if name == "total" else f"seg.{name}", value)
        if "total" in segments:
            STATS.add(f"kind.{turn.kind}.total", segments["total"])


# Numeric facts a turn's latency line carries beside its segments (see adapter.py), summarised
# as ``note.<name>``.
_NOTE_METRICS = (
    "vad_silence_ms",
    "endpoint_hold_ms",
    "tts_phrase_buffer_ms",
    "tts_send_to_audio_ms",
    "tts_chars_at_first_chunk",
)


def summarize_events(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate parsed ``voice_turn_latency`` / ``voice_interruption_latency``
    log events (used by scripts/voice_latency_report.py)."""
    stats = RollingStats(window=10**9)
    for ev in events:
        name = ev.get("event")
        if name == "voice_turn_latency":
            for seg, value in (ev.get("segments_ms") or {}).items():
                if isinstance(value, (int, float)):
                    stats.add("total" if seg == "total" else f"seg.{seg}", value)
            for key in _NOTE_METRICS:
                if isinstance(ev.get(key), (int, float)):
                    stats.add(f"note.{key}", ev[key])
        elif name == "voice_interruption_latency":
            for key, value in ev.items():
                if key.endswith("_ms") and isinstance(value, (int, float)):
                    stats.add(key, value)
        elif name == "voice_metric":
            value = ev.get("value_ms")
            if isinstance(value, (int, float)) and isinstance(ev.get("metric"), str):
                stats.add(ev["metric"], value)
        elif name == "voice_cancel_latency":
            value = ev.get("cancel_to_done_ms")
            if isinstance(value, (int, float)):
                stats.add("cancel_to_done_ms", value)
        elif name == "voice_turn_playback":
            value = ev.get("est_playback_ms")
            if isinstance(value, (int, float)):
                stats.add("est_playback_ms", value)
    return stats.snapshot()


def endpoint_category_counts(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many turns fell in each adaptive-endpointing category (complete, unfinished, ...)."""
    counts: dict[str, int] = {}
    for ev in events:
        if ev.get("event") == "voice_turn_latency" and isinstance(ev.get("endpoint_category"), str):
            counts[ev["endpoint_category"]] = counts.get(ev["endpoint_category"], 0) + 1
    return counts


def turn_event_counts(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts of ``voice_turn_event`` kinds, with the turns they occurred in, so a rate per 100 turns can be
    read off. These are COUNTS of things the adapter suspected or prevented, not measured quality:
    ``premature_response_suspected`` and ``false_interruption`` are heuristics (see adapter.py), and
    ``muted_audio_dropped`` / ``stale_event_dropped`` are stale audio that was kept from the caller."""
    counts: dict[str, int] = {}
    turns = 0
    for ev in events:
        name = ev.get("event")
        if name == "voice_turn_latency":
            turns += 1
        elif name == "voice_turn_event" and isinstance(ev.get("kind"), str):
            counts[ev["kind"]] = counts.get(ev["kind"], 0) + 1
    per_100 = {k: round(100 * v / turns, 1) for k, v in counts.items()} if turns else {}
    return {"turns": turns, "counts": counts, "per_100_turns": per_100}
