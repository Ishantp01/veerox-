"""SHADOW-MODE timing-based ownership of live partial transcripts (``VOICE_LIVE_SHADOW_OWNERSHIP``).

Why: the live transcriber (``gpt-live-transcribe``) reuses ONE ``item_id`` for a whole connection and emits no
``.completed`` events (measured), so ``adapter._live_delta_is_current`` (first turn to see an id owns it) treats every
later turn's text as stale. This module attributes each delta by *when this process handled it* instead.

Every delta gets exactly one decision of four kinds, counted separately and never mixed:

* ``confirmed``    - exactly one candidate turn: the open turn, or a committed turn inside its grace window;
* ``ambiguous``    - two candidates (the previous turn's tail or the new turn's first words): a delta that arrives
                     soon after a NEW turn started while the previous turn was committed recently. It is credited to
                     neither turn, never counted as agreement with the item-id rule, and not used for any timing;
* ``rejected``     - the only candidate turn is known to be dead (its open-turn timeout has expired);
* ``unassignable`` - no turn can own it (before any turn, or idle after the grace window).

Ambiguity is NOT exhaustive: a tail that arrives after the ambiguity window, a delta inside a grace window while the
next ``speech_started`` is still unread, and a call's first turn can all be mis-attributed without being flagged.
``ambiguous`` counts are therefore a lower bound on uncertain attributions.

A turn opens on ``speech_started`` (a repeat before the commit is the same turn), is committed by ``committed`` (grace
window starts) and ends when the next turn starts. An open turn that never commits is abandoned (``open_turn_timeouts``)
after ``open_turn_timeout_s`` without activity, or ``stopped_commit_wait_s`` after ``speech_stopped``, so later speech
is never merged into it indefinitely.

It only OBSERVES. It never cuts, clears, delays or changes any text, and holds no text (lengths only). Synchronous
with no awaits: on the single event loop each call is atomic, so no lock is needed. All timestamps are
``time.monotonic()`` taken when THIS process handled an event; they are not provider timestamps. The two sockets
(main Realtime session, live transcriber) are read by different tasks with no shared ordering, and the main session
handles its events strictly one at a time, so a delta can be handled before an earlier ``speech_started`` or
``committed`` that is still unread. ``main_begin`` / ``main_end`` measure how long main-session handlers run and how
many deltas were handled while one was in flight, to size that skew.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_GRACE_S = 1.5
DEFAULT_MIN_LAG_S = 0.5
DEFAULT_PREV_COMMIT_RECENCY_S = (
    3.0  # a previous turn committed this recently can still have a tail arriving
)
DEFAULT_OPEN_TURN_TIMEOUT_S = 20.0  # an open turn with no activity for this long is abandoned
DEFAULT_STOPPED_COMMIT_WAIT_S = 5.0  # speech_stopped with no commit for this long: abandoned
AMBIGUOUS_WINDOW_S = 1.0  # weaker doubt: soon after a new start, but later than min_lag
ORPHAN_WINDOW_S = 0.5  # deltas seen just BEFORE a late speech_started are counted on that turn, never moved into it
SLOW_MAIN_HANDLER_S = (
    0.1  # a main-session handler running longer than this holds up the events behind it
)
BACKLOG_GAP_S = 0.002  # next event read this soon after a slow handler finished: it was probably already buffered
MAX_DECISIONS = 500
MAX_TURNS = 64

# Bounded vocabulary for handler timing, so no free text can become a counter name.
_HANDLER_BUCKETS = {
    "input_audio_buffer.speech_started": "speech_started",
    "input_audio_buffer.speech_stopped": "speech_stopped",
    "input_audio_buffer.committed": "committed",
    "conversation.item.input_audio_transcription.completed": "transcript_completed",
    "response.text.delta": "text_delta",
    "response.output_text.delta": "text_delta",
    "response.done": "response_done",
}


@dataclass
class _Turn:
    id: int
    started_at: float
    last_activity_at: float
    agent_busy: bool = False
    synthetic: bool = False  # opened by a commit that had no speech_started (event missing or late)
    abandoned: bool = False  # never committed; closed by the open-turn timeout
    restarts: int = 0
    stopped_at: float | None = None
    committed_at: float | None = None
    closed_at: float | None = None  # the next speech_started: its grace window ended here
    confirmed_open: int = 0
    confirmed_grace: int = 0
    ambiguous: int = 0
    orphans_before_start: int = 0
    chars: int = 0  # confirmed deltas only
    ambiguous_chars: int = 0
    first_delta_ms: int | None = None  # confirmed deltas only; since started_at


@dataclass(frozen=True)
class Decision:
    kind: str  # confirmed | ambiguous | rejected | unassignable
    reason: str
    turn_id: (
        int | None
    )  # the single owning turn (confirmed) or the dead turn (rejected); None otherwise
    candidates: tuple[int, ...]  # for ambiguous: the turns it could belong to
    phase: str | None  # open | grace | None
    legacy_accepted: bool  # what the item-id rule decided
    arrival: float
    since_start_ms: int | None
    since_commit_ms: int | None
    text_len: int
    item_id_present: bool
    main_inflight_ms: (
        int | None
    )  # how long the main-session handler running at this moment had been running

    @property
    def confirmed(self) -> bool:
        return self.kind == "confirmed"

    @property
    def agreement(self) -> bool | None:
        """Does the shadow decision match the item-id rule? None for ambiguous: never counted as agreement."""
        if self.kind == "ambiguous":
            return None
        return self.confirmed == self.legacy_accepted


@dataclass
class ShadowOwnership:
    grace_s: float = DEFAULT_GRACE_S
    min_lag_s: float = DEFAULT_MIN_LAG_S
    prev_commit_recency_s: float = DEFAULT_PREV_COMMIT_RECENCY_S
    open_turn_timeout_s: float = DEFAULT_OPEN_TURN_TIMEOUT_S
    stopped_commit_wait_s: float = DEFAULT_STOPPED_COMMIT_WAIT_S
    clock: Callable[[], float] = time.monotonic
    decisions: deque[Decision] = field(default_factory=lambda: deque(maxlen=MAX_DECISIONS))
    turns: dict[int, _Turn] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    handler_stats: dict[str, list[int]] = field(
        default_factory=dict
    )  # bucket -> [count, max_ms, over_slow]
    _current: _Turn | None = None
    _seq: int = 0
    _idle_unassignable_at: deque[float] = field(default_factory=lambda: deque(maxlen=32))
    _main_started_at: float | None = None
    _main_prev_end: float | None = None
    _main_prev_duration: float = 0.0
    _main_bucket: str = "other"

    def __post_init__(self) -> None:
        self.grace_s = min(5.0, max(0.0, self.grace_s))
        self.min_lag_s = min(2.0, max(0.0, self.min_lag_s))
        self.prev_commit_recency_s = min(30.0, max(0.0, self.prev_commit_recency_s))
        self.open_turn_timeout_s = min(300.0, max(1.0, self.open_turn_timeout_s))
        self.stopped_commit_wait_s = min(60.0, max(0.5, self.stopped_commit_wait_s))

    # -- lifecycle ---------------------------------------------------------------------------------------------

    def _bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def _new_turn(self, now: float, *, agent_busy: bool, synthetic: bool = False) -> _Turn:
        self._seq += 1
        turn = _Turn(
            id=self._seq,
            started_at=now,
            last_activity_at=now,
            agent_busy=agent_busy,
            synthetic=synthetic,
        )
        self.turns[turn.id] = turn
        while len(self.turns) > MAX_TURNS:
            self.turns.pop(next(iter(self.turns)))
        self._current = turn
        return turn

    def _is_open(self, turn: _Turn | None) -> bool:
        return turn is not None and turn.committed_at is None and turn.closed_at is None

    def _expired(self, turn: _Turn, now: float) -> bool:
        """An open turn whose commit never came: silent too long, or speech_stopped with no commit for too long."""
        if not self._is_open(turn):
            return False
        if turn.stopped_at is not None and now - turn.stopped_at > self.stopped_commit_wait_s:
            return True
        return now - turn.last_activity_at > self.open_turn_timeout_s

    def _abandon(self, turn: _Turn, now: float) -> None:
        turn.abandoned = True
        turn.closed_at = now
        self._bump("open_turn_timeouts")

    def on_speech_started(self, *, agent_busy: bool = False) -> int:
        """The provider VAD heard the caller. A new turn unless the open one has not been committed yet."""
        now = self.clock()
        cur = self._current
        if cur is not None and self._is_open(cur):
            if not self._expired(cur, now):
                cur.restarts += 1  # the VAD re-fired inside one continuous turn
                cur.stopped_at = None
                cur.last_activity_at = now
                self._bump("speech_restarts")
                return cur.id
            self._abandon(
                cur, now
            )  # the commit never came: do not merge this speech into the dead turn
        elif cur is not None and cur.closed_at is None:
            cur.closed_at = (
                now  # the previous turn's grace window ends here, whatever time it had left
            )
        turn = self._new_turn(now, agent_busy=agent_busy)
        turn.orphans_before_start = sum(
            1 for t in self._idle_unassignable_at if 0 <= now - t <= ORPHAN_WINDOW_S
        )
        if turn.orphans_before_start:
            self._bump("orphan_deltas_before_start", turn.orphans_before_start)
        self._bump("turns")
        return turn.id

    def on_speech_stopped(self) -> None:
        """The provider VAD reports silence. Not a commit: only starts the 'commit is overdue' clock."""
        now = self.clock()
        cur = self._current
        if cur is not None and self._is_open(cur):
            cur.stopped_at = now

    def on_committed(self) -> int:
        """The provider committed the caller's audio: the open turn's speech is over, its grace window starts."""
        now = self.clock()
        cur = self._current
        if cur is not None and self._is_open(cur) and self._expired(cur, now):
            self._abandon(
                cur, now
            )  # this commit belongs to speech whose speech_started we never saw
        if cur is None or cur.committed_at is not None or cur.closed_at is not None:
            # No open turn: the speech_started was missing or is still queued. Keep the commit's timing anyway.
            cur = self._new_turn(now, agent_busy=False, synthetic=True)
            self._bump("commits_without_open_turn")
        cur.committed_at = now
        return cur.id

    # -- the decision ------------------------------------------------------------------------------------------

    def on_delta(self, text_len: int, *, item_id: str | None, legacy_accepted: bool) -> Decision:
        now = self.clock()
        cur = self._current
        kind, reason = "unassignable", "no_turn_yet"
        turn_id: int | None = None
        candidates: tuple[int, ...] = ()
        phase: str | None = None
        since_start = since_commit = None
        if cur is not None:
            since_start = round((now - cur.started_at) * 1000)
            since_commit = (
                None if cur.committed_at is None else round((now - cur.committed_at) * 1000)
            )
            prev = self.turns.get(cur.id - 1)
            if self._is_open(cur) and self._expired(cur, now):
                kind, reason, turn_id = "rejected", "open_turn_expired", cur.id
            elif self._is_open(cur):
                lag = now - cur.started_at
                # Evidence that the previous turn can still have a tail arriving: it was committed recently.
                prev_recent = (
                    prev is not None
                    and prev.committed_at is not None
                    and now - prev.committed_at <= self.prev_commit_recency_s
                )
                if prev_recent and lag < self.min_lag_s:
                    kind, reason = "ambiguous", "possible_previous_tail"
                elif prev_recent and lag < AMBIGUOUS_WINDOW_S:
                    kind, reason = "ambiguous", "early_after_recent_turn"
                else:
                    kind, reason, turn_id, phase = "confirmed", "open_turn", cur.id, "open"
                if kind == "ambiguous" and prev is not None:
                    candidates = (prev.id, cur.id)
            elif (
                cur.committed_at is not None
                and cur.closed_at is None
                and now - cur.committed_at <= self.grace_s
            ):
                kind, reason, turn_id, phase = "confirmed", "grace_window", cur.id, "grace"
            else:
                kind, reason = "unassignable", "idle_after_grace"
        if kind == "unassignable":
            self._idle_unassignable_at.append(now)
        if cur is not None and kind in ("confirmed", "ambiguous"):
            cur.last_activity_at = (
                now  # the caller is evidently still talking: keeps the turn alive
            )
        if kind == "confirmed" and cur is not None:
            if phase == "open":
                cur.confirmed_open += 1
            else:
                cur.confirmed_grace += 1
            cur.chars += max(0, text_len)
            if cur.first_delta_ms is None:
                cur.first_delta_ms = since_start
        elif kind == "ambiguous" and cur is not None:
            cur.ambiguous += 1
            cur.ambiguous_chars += max(0, text_len)
        inflight_ms = (
            None if self._main_started_at is None else round((now - self._main_started_at) * 1000)
        )
        decision = Decision(
            kind=kind,
            reason=reason,
            turn_id=turn_id,
            candidates=candidates,
            phase=phase,
            legacy_accepted=legacy_accepted,
            arrival=now,
            since_start_ms=since_start,
            since_commit_ms=since_commit,
            text_len=text_len,
            item_id_present=bool(item_id),
            main_inflight_ms=inflight_ms,
        )
        self.decisions.append(decision)
        self._bump("deltas_total")
        self._bump(f"{kind}_total")
        self._bump(f"{kind}:{reason}")
        agreement = decision.agreement
        if agreement is None:
            self._bump(
                "ambiguous_legacy_accepted" if legacy_accepted else "ambiguous_legacy_rejected"
            )
        else:
            self._bump("agreed_total" if agreement else "disagreed_total")
        if not item_id:
            self._bump("deltas_without_item_id")
        if inflight_ms is not None and inflight_ms >= SLOW_MAIN_HANDLER_S * 1000:
            self._bump("deltas_during_slow_main_handler")
        return decision

    # -- main-session handler timing (processing time only; NOT provider timestamps) -----------------------------

    def main_begin(self, event_type: str | None) -> None:
        now = self.clock()
        if (
            self._main_prev_end is not None
            and now - self._main_prev_end <= BACKLOG_GAP_S
            and self._main_prev_duration >= SLOW_MAIN_HANDLER_S
        ):
            # The next event was read the instant a slow handler finished: it was almost certainly already waiting.
            self._bump("main_reads_after_slow_handler_without_wait")
        self._main_started_at = now
        self._main_bucket = _HANDLER_BUCKETS.get(event_type or "", "other")

    def main_end(self) -> None:
        started, self._main_started_at = self._main_started_at, None
        if started is None:
            return
        now = self.clock()
        duration = max(0.0, now - started)
        stat = self.handler_stats.setdefault(self._main_bucket, [0, 0, 0])
        stat[0] += 1
        stat[1] = max(stat[1], round(duration * 1000))
        if duration >= SLOW_MAIN_HANDLER_S:
            stat[2] += 1
        self._main_prev_end, self._main_prev_duration = now, duration

    # -- reporting (counts and timings only; never text) -------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(sorted(self.counts.items()))
        out["turns_tracked"] = len(self.turns)
        out["turns_abandoned"] = sum(1 for t in self.turns.values() if t.abandoned)
        # Timing from CONFIRMED deltas only; ambiguous ones can be a previous turn's tail and would bias it low.
        firsts = sorted(
            t.first_delta_ms for t in self.turns.values() if t.first_delta_ms is not None
        )
        if firsts:
            out["confirmed_first_delta_ms_p50"] = firsts[len(firsts) // 2]
            out["confirmed_first_delta_ms_max"] = firsts[-1]
        out["ambiguous_is_lower_bound"] = True
        for bucket, (n, worst, slow) in sorted(self.handler_stats.items()):
            out[f"main_handler_{bucket}_count"] = n
            out[f"main_handler_{bucket}_max_ms"] = worst
            out[f"main_handler_{bucket}_slow_count"] = slow
        return out
