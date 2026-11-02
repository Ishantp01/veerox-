"""SHADOW-MODE timing-based ownership of live transcripts (channels/voice/live_ownership_shadow.py).

Pure-tracker tests use a fake clock. Integration tests drive the real adapter handlers and the real Realtime pump with
mocked events and check (a) what the shadow decided and (b) that turning it on changes nothing the caller hears or
the text the agent uses."""

from __future__ import annotations

import asyncio
import json
import time
import types
from typing import Any

import pytest

from apps.api.channels.voice import adapter
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice.live_ownership_shadow import ShadowOwnership
from apps.api.config import settings
from apps.api.tests.test_voice_barge_in_turns import _agent_speaking, _timed_call
from apps.api.tests.test_voice_end_of_turn_latency import (  # noqa: F401
    _long_answer_still_playing,
    offline_judge,
)
from apps.api.tests.test_voice_interruption import Call, _flags, base  # noqa: F401


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def at(self, t: float) -> None:
        self.t = 100.0 + t


def tracker(**kw: Any) -> tuple[ShadowOwnership, Clock]:
    clock = Clock()
    return ShadowOwnership(clock=clock, **kw), clock


def delta(sh: ShadowOwnership, item: str | None = "X", n: int = 4, legacy: bool = True):
    return sh.on_delta(n, item_id=item, legacy_accepted=legacy)


def turn_with_commit(sh: ShadowOwnership, c: Clock, start: float, commit: float) -> None:
    c.at(start)
    sh.on_speech_started()
    c.at(commit)
    sh.on_committed()


# --- confirmed / grace ------------------------------------------------------------------------------------------


def test_normal_speech_then_a_delayed_final_delta_inside_the_grace_window() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    c.at(0.9)
    assert delta(sh).reason == "open_turn"
    c.at(1.4)
    sh.on_committed()
    c.at(2.0)
    d = delta(sh)  # the transcriber lags the audio: this still belongs to turn 1
    assert (d.kind, d.reason, d.turn_id, d.phase) == ("confirmed", "grace_window", 1, "grace")
    c.at(3.2)
    d = delta(sh)  # well past the grace window, nobody is speaking
    assert (d.kind, d.reason, d.turn_id) == ("unassignable", "idle_after_grace", None)


# --- 2. minimum-lag rule: evidence before doubt, doubt is ambiguous not rejected --------------------------------------


def test_a_genuine_fast_first_delta_is_confirmed_when_the_previous_turn_is_old() -> None:
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(10.0)
    sh.on_speech_started()  # the previous commit was 9 s ago: far outside the recency window
    c.at(10.2)  # 200 ms after the start - under the old 500 ms rule this was rejected
    d = delta(sh)
    assert (d.kind, d.reason, d.turn_id, d.phase) == ("confirmed", "open_turn", 2, "open")
    assert sh.turns[2].chars == 4 and sh.turns[2].first_delta_ms == 200


def test_a_fast_first_delta_on_the_very_first_turn_is_confirmed() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    c.at(0.2)
    assert delta(sh).kind == "confirmed"


def test_a_delayed_old_turn_delta_after_a_new_start_is_ambiguous_not_rejected_and_not_given_to_the_new_turn() -> (
    None
):
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(1.3)
    sh.on_speech_started()  # turn 2 begins 0.3 s after turn 1's commit
    c.at(1.5)  # 0.2 s after the new start, 0.5 s after the old commit
    d = delta(sh, n=9)
    assert (d.kind, d.reason, d.turn_id, d.candidates) == (
        "ambiguous",
        "possible_previous_tail",
        None,
        (1, 2),
    )
    assert (
        sh.turns[2].chars == 0 and sh.turns[2].first_delta_ms is None
    )  # not credited to the new turn
    assert (
        sh.turns[1].chars == 0 and sh.turns[1].confirmed_grace == 0
    )  # nor confirmed for the old one
    assert sh.turns[2].ambiguous == 1 and sh.turns[2].ambiguous_chars == 9
    assert sh.counts.get("rejected_total", 0) == 0


def test_ambiguity_ends_after_the_window_and_the_delta_is_confirmed() -> None:
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(1.3)
    sh.on_speech_started()
    c.at(1.9)  # 0.6 s after the start: past min lag, inside the weaker window
    assert delta(sh).reason == "early_after_recent_turn"
    c.at(2.5)  # 1.2 s after the start
    assert delta(sh).kind == "confirmed"


def test_the_previous_commit_recency_window_is_configurable() -> None:
    results = {}
    for recency in (0.4, 3.0):
        sh, c = tracker(prev_commit_recency_s=recency)
        turn_with_commit(sh, c, 0.0, 1.0)
        c.at(1.3)
        sh.on_speech_started()
        c.at(1.5)  # the old commit was 0.5 s ago
        results[recency] = delta(sh).kind
    assert results == {0.4: "confirmed", 3.0: "ambiguous"}


def test_limitation_a_late_tail_after_the_ambiguity_window_is_confirmed_for_the_new_turn() -> None:
    """The timing model cannot tell a very late old tail from the new turn's words once the doubt window has passed:
    that is why the ambiguous count is a lower bound (see the module docstring)."""
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(1.3)
    sh.on_speech_started()
    c.at(2.6)  # 1.3 s after the new start: a 1.6 s-late tail looks exactly like this
    assert delta(sh).kind == "confirmed"
    assert sh.summary()["ambiguous_is_lower_bound"] is True


# --- 1. ambiguous deltas never contaminate the first-delta metric ------------------------------------------------------


def test_ambiguous_deltas_cannot_pull_the_first_delta_p50_down() -> None:
    sh, c = tracker()
    turn_with_commit(
        sh, c, 0.0, 0.5
    )  # a priming turn, so every measured turn follows a recent commit
    t = 1.2
    for _ in range(3):
        c.at(t)
        sh.on_speech_started()
        c.at(t + 0.2)
        assert delta(sh, n=3).kind == "ambiguous"  # an old tail landing 200 ms in
        c.at(t + 1.1)
        assert delta(sh, n=5).kind == "confirmed"  # the turn's real first words, 1.1 s in
        c.at(t + 1.5)
        sh.on_committed()
        t += 2.2
    firsts = [x.first_delta_ms for x in sh.turns.values() if x.first_delta_ms is not None]
    assert firsts == [1100, 1100, 1100]  # none of the 200 ms deltas
    summary = sh.summary()
    assert (
        summary["confirmed_first_delta_ms_p50"] == 1100
        and summary["confirmed_first_delta_ms_max"] == 1100
    )
    assert [x.chars for x in sh.turns.values()] == [
        0,
        5,
        5,
        5,
    ]  # characters from confirmed deltas only
    assert (
        sum(x.ambiguous_chars for x in sh.turns.values()) == 9 and summary["ambiguous_total"] == 3
    )


def test_ambiguous_decisions_are_not_counted_as_agreement_or_disagreement() -> None:
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(1.3)
    sh.on_speech_started()
    c.at(1.5)
    d_acc = delta(sh, legacy=True)
    d_rej = delta(sh, legacy=False)
    assert (
        d_acc.kind == d_rej.kind == "ambiguous"
        and d_acc.agreement is None
        and d_rej.agreement is None
    )
    assert sh.counts.get("agreed_total", 0) == 0 and sh.counts.get("disagreed_total", 0) == 0
    assert (
        sh.counts["ambiguous_legacy_accepted"] == 1 and sh.counts["ambiguous_legacy_rejected"] == 1
    )


def test_summary_separates_the_four_kinds_and_holds_no_text() -> None:
    sh, c = tracker()
    delta(sh)  # unassignable: no turn yet
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(1.2)
    delta(sh)  # confirmed (grace)
    c.at(1.3)
    sh.on_speech_started()
    c.at(1.4)
    delta(sh)  # ambiguous
    c.at(40.0)
    delta(sh)  # the open turn is dead by now: rejected
    s = sh.summary()
    assert (
        s["confirmed_total"],
        s["ambiguous_total"],
        s["rejected_total"],
        s["unassignable_total"],
    ) == (1, 1, 1, 1)
    assert s["deltas_total"] == 4
    assert not any(isinstance(v, str) for v in s.values())
    assert "text" not in sh.decisions[0].__dataclass_fields__


# --- 3. permanently open turns ----------------------------------------------------------------------------------------


def test_a_turn_whose_commit_never_arrives_does_not_swallow_later_speech() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    c.at(1.0)
    delta(sh)
    c.at(30.0)  # no commit, no speech_stopped, 29 s of silence
    new_id = sh.on_speech_started()
    assert new_id == 2 and sh.turns[1].abandoned and sh.counts["open_turn_timeouts"] == 1
    c.at(31.0)
    d = delta(sh)
    assert (d.kind, d.turn_id) == (
        "confirmed",
        2,
    )  # the abandoned turn is not "recent": no ambiguity either


def test_a_turn_with_continuing_deltas_stays_alive_through_a_long_monologue() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    for t in range(5, 65, 5):  # 60 s of talking, a delta every 5 s
        c.at(float(t))
        assert delta(sh).kind in ("confirmed", "ambiguous")
    c.at(66.0)
    assert sh.on_speech_started() == 1 and sh.counts.get("open_turn_timeouts", 0) == 0


def test_without_speech_stopped_the_activity_timeout_is_the_only_guard() -> None:
    """speech_stopped may never arrive (the repo already handles turns without it): the timeout still protects."""
    sh, c = tracker(open_turn_timeout_s=10.0)
    c.at(0.0)
    sh.on_speech_started()
    c.at(9.0)
    assert sh.on_speech_started() == 1  # still within the timeout: the VAD re-fired inside one turn
    c.at(25.0)
    assert sh.on_speech_started() == 2


def test_speech_stopped_without_a_commit_shortens_the_wait_and_a_restart_inside_it_is_the_same_turn() -> (
    None
):
    sh, c = tracker(stopped_commit_wait_s=5.0)
    c.at(0.0)
    sh.on_speech_started()
    c.at(2.0)
    sh.on_speech_stopped()
    c.at(5.0)  # 3 s after the stop: the commit may simply be queued behind a slow handler
    assert (
        sh.on_speech_started() == 1 and sh.turns[1].stopped_at is None
    )  # caller resumed: stop cleared
    c.at(6.0)
    sh.on_speech_stopped()
    c.at(12.0)  # 6 s after the stop and still no commit: abandoned
    assert sh.on_speech_started() == 2 and sh.turns[1].abandoned


def test_a_delta_for_an_expired_open_turn_is_rejected_then_recovery_works() -> None:
    sh, c = tracker(stopped_commit_wait_s=2.0)
    c.at(0.0)
    sh.on_speech_started()
    c.at(1.0)
    sh.on_speech_stopped()
    c.at(4.0)
    d = delta(sh)  # the commit is 3 s overdue
    assert (d.kind, d.reason, d.turn_id) == ("rejected", "open_turn_expired", 1)
    c.at(5.0)
    sh.on_speech_started()  # recovery: a fresh turn
    sh.on_committed()
    c.at(5.5)
    assert delta(sh).turn_id == 2


def test_a_commit_for_an_expired_turn_opens_a_synthetic_turn_instead_of_extending_the_dead_one() -> (
    None
):
    sh, c = tracker(open_turn_timeout_s=5.0)
    c.at(0.0)
    sh.on_speech_started()
    c.at(20.0)
    assert sh.on_committed() == 2 and sh.turns[1].abandoned and sh.turns[2].synthetic
    c.at(20.5)
    assert delta(sh).turn_id == 2


# --- the rest of the rules (unchanged) -------------------------------------------------------------------------------------


def test_repeated_speech_started_inside_one_turn_is_the_same_turn() -> None:
    sh, c = tracker()
    c.at(0.0)
    assert sh.on_speech_started() == 1
    c.at(0.8)
    assert sh.on_speech_started() == 1  # VAD re-fired before any commit
    c.at(1.2)
    assert delta(sh).turn_id == 1
    assert sh.counts["turns"] == 1 and sh.counts["speech_restarts"] == 1


def test_missing_item_ids_are_attributed_by_time_and_counted() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    c.at(1.0)
    d = delta(sh, item=None)
    assert d.confirmed and not d.item_id_present and sh.counts["deltas_without_item_id"] == 1


def test_delta_before_any_speech_started_is_unassignable_not_guessed() -> None:
    sh, c = tracker()
    c.at(0.0)
    d = delta(sh)
    assert (d.kind, d.reason) == ("unassignable", "no_turn_yet")


def test_a_commit_without_a_speech_started_opens_a_synthetic_turn_with_a_grace_window() -> None:
    sh, c = tracker()
    c.at(0.0)
    delta(sh)  # unassignable
    c.at(1.0)
    sh.on_committed()  # speech_started missing
    c.at(1.8)
    d = delta(sh)
    assert d.confirmed and d.phase == "grace" and sh.turns[d.turn_id].synthetic
    assert sh.counts["commits_without_open_turn"] == 1


def test_deltas_seen_just_before_a_late_speech_started_are_counted_never_moved() -> None:
    sh, c = tracker()
    c.at(0.0)
    d1 = delta(sh)
    c.at(0.1)
    delta(sh)
    c.at(0.3)
    sh.on_speech_started()
    assert sh.turns[1].orphans_before_start == 2 and sh.counts["orphan_deltas_before_start"] == 2
    assert sh.turns[1].chars == 0 and not d1.confirmed  # the recorded decision is not rewritten


def test_a_duplicate_commit_does_not_extend_the_grace_window() -> None:
    sh, c = tracker()
    turn_with_commit(sh, c, 0.0, 1.0)
    c.at(2.0)
    sh.on_committed()  # redelivered: a commit with no open turn, never re-arms turn 1
    c.at(2.4)
    d = delta(sh)
    assert d.turn_id != 1 or d.since_commit_ms <= 1500


# --- 5. event-loop ordering instrumentation -----------------------------------------------------------------------------


def test_deltas_handled_during_a_slow_main_handler_are_counted_with_how_long_it_had_run() -> None:
    sh, c = tracker()
    c.at(0.0)
    sh.on_speech_started()
    c.at(1.0)
    sh.main_begin("response.text.delta")  # e.g. blocked in asyncio.wait on the TTS drain
    c.at(1.4)
    d = delta(sh)
    assert d.main_inflight_ms == 400 and sh.counts["deltas_during_slow_main_handler"] == 1
    c.at(2.0)
    sh.main_end()
    c.at(2.1)
    assert delta(sh).main_inflight_ms is None
    s = sh.summary()
    assert (
        s["main_handler_text_delta_max_ms"] == 1000 and s["main_handler_text_delta_slow_count"] == 1
    )


def test_a_fast_handler_is_not_counted_slow_and_unknown_event_types_use_a_bounded_bucket() -> None:
    sh, c = tracker()
    for etype in ("input_audio_buffer.committed", "some.free.text.type"):
        sh.main_begin(etype)
        c.t += 0.01
        sh.main_end()
    s = sh.summary()
    assert s["main_handler_committed_slow_count"] == 0 and s["main_handler_other_count"] == 1
    assert not any("free" in k for k in s)


def test_a_read_that_follows_a_slow_handler_with_no_wait_is_flagged_as_probably_buffered() -> None:
    sh, c = tracker()
    sh.main_begin("response.text.delta")
    c.t += 0.5
    sh.main_end()
    sh.main_begin(
        "input_audio_buffer.speech_started"
    )  # read the same instant: it was already waiting
    sh.main_end()
    assert sh.counts["main_reads_after_slow_handler_without_wait"] == 1
    sh.main_begin("input_audio_buffer.committed")  # a quiet gap after a fast handler: not flagged
    c.t += 0.01
    sh.main_end()
    c.t += 1.0
    sh.main_begin("response.done")
    assert sh.counts["main_reads_after_slow_handler_without_wait"] == 1


# --- through the real adapter handlers and the real pump ----------------------------------------------------------------------


async def live(call: Call, text: str, item_id: str | None = "ONE") -> None:
    await adapter.handle_live_delta(
        text, call.phone, call.agent, call.state, call.log, item_id=item_id
    )  # type: ignore[arg-type]


async def scenario(call: Call) -> dict[str, Any]:
    """Two caller turns over a long answer; one live item id for the whole connection (what the provider does)."""
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    await live(call, "mujhe franchise ka")
    await live(call, " price batao")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="c1")
    await asyncio.sleep(0.15)
    await call.event("input_audio_buffer.speech_started")
    await live(call, " ji")  # a tail of turn 1 landing in turn 2
    await asyncio.sleep(0.55)
    await live(call, "aur timing kya hai")
    await asyncio.sleep(0.05)
    out = {
        "text": call.state.utterance_text,
        "cleared": call.cleared(),
        "replies": len(call.replies()),
    }
    await adapter.cancel_pending_tasks(call.state)
    return out


async def test_turning_the_shadow_on_changes_nothing_the_caller_or_the_agent_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = {}
    for flag in (False, True):
        _flags(monkeypatch, voice_live_shadow_ownership=flag, voice_commit_time_cut=True)
        call = _timed_call(live=types.SimpleNamespace(healthy=True))
        results[flag] = await scenario(call)
        assert (call.state.live_shadow is not None) == flag
    assert results[False] == results[True]


async def test_one_item_id_across_turns_shadow_confirms_what_the_item_id_rule_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    shadow = adapter._shadow(call.state)
    clock = Clock()
    shadow.clock = clock
    await _agent_speaking(call)
    clock.at(0.0)
    await call.event("input_audio_buffer.speech_started")
    clock.at(1.0)
    await live(call, "pehla sawaal")  # item ONE is claimed by turn 1: both rules accept
    await call.event("input_audio_buffer.speech_stopped")
    clock.at(1.1)
    await call.event("input_audio_buffer.committed", item_id="c1")
    clock.at(9.0)
    await call.event("input_audio_buffer.speech_started")
    clock.at(10.2)
    await live(call, "doosra sawaal")  # same item id, turn 2: the legacy rule calls it stale
    last = shadow.decisions[-1]
    assert (
        last.confirmed
        and last.turn_id == 2
        and last.legacy_accepted is False
        and last.agreement is False
    )
    assert shadow.counts["disagreed_total"] == 1 and shadow.counts["agreed_total"] >= 1
    assert (
        "doosra" not in call.state.utterance_text
    )  # the legacy decision still wins: behaviour unchanged
    await adapter.cancel_pending_tasks(call.state)


async def test_overlap_with_agent_audio_is_recorded_without_the_shadow_acting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _agent_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    assert call.state.live_shadow.turns[1].agent_busy is True
    await live(call, "haan ji")
    assert call.state.live_shadow.decisions[-1].confirmed
    assert not call.cleared()  # the shadow recorded the overlap; it did not cut anything
    await adapter.cancel_pending_tasks(call.state)


async def test_speech_stopped_reaches_the_tracker_through_the_real_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    assert call.state.live_shadow.turns[1].stopped_at is not None
    await adapter.cancel_pending_tasks(call.state)


async def test_noise_text_is_only_attributed_never_filtered_or_acted_on_by_the_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for flag in (False, True):
        _flags(
            monkeypatch,
            voice_live_shadow_ownership=flag,
            voice_commit_time_cut=True,
            voice_smart_turn_taking=True,
        )
        call = _timed_call(live=types.SimpleNamespace(healthy=True))
        await _long_answer_still_playing(call)
        await call.event("input_audio_buffer.speech_started")
        await live(call, "aaaa")
        await call.event("input_audio_buffer.speech_stopped")
        await call.event("input_audio_buffer.committed", item_id=f"n{flag}")
        await asyncio.sleep(0.15)
        assert not call.cleared()  # same as without the shadow
        await adapter.cancel_pending_tasks(call.state)


async def test_concurrent_deltas_and_commits_get_exactly_one_decision_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")

    async def deltas(n: int) -> None:
        for i in range(n):
            await live(call, f"w{i}")
            await asyncio.sleep(0)

    async def commits() -> None:
        for i in range(3):
            await call.event("input_audio_buffer.speech_stopped")
            await call.event("input_audio_buffer.committed", item_id=f"c{i}")
            await call.event("input_audio_buffer.speech_started")
            await asyncio.sleep(0)

    await asyncio.gather(deltas(40), commits(), deltas(40))
    sh = call.state.live_shadow
    kinds = ("confirmed", "ambiguous", "rejected", "unassignable")
    assert len(sh.decisions) == 80 and sh.counts["deltas_total"] == 80
    assert sum(sh.counts.get(f"{k}_total", 0) for k in kinds) == 80
    assert (
        sum(sh.counts.get(k, 0) for k in ("agreed_total", "disagreed_total"))
        + sum(
            sh.counts.get(k, 0) for k in ("ambiguous_legacy_accepted", "ambiguous_legacy_rejected")
        )
        == 80
    )
    await adapter.cancel_pending_tasks(call.state)


async def test_a_broken_tracker_never_touches_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = Call(live=types.SimpleNamespace(healthy=True))

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("tracker bug")

    await call.event("input_audio_buffer.speech_started")
    for name in ("on_delta", "on_committed", "on_speech_stopped", "main_begin", "main_end"):
        monkeypatch.setattr(call.state.live_shadow, name, boom)
    adapter.shadow_event_begin(call.state, {"type": "x"})
    adapter.shadow_event_end(call.state)
    await live(call, "price batao")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="c1")
    assert call.state.utterance_text == "price batao"
    await adapter.cancel_pending_tasks(call.state)


async def test_flag_off_creates_no_tracker_and_the_defaults_are_off() -> None:
    assert settings.voice_live_shadow_ownership is False
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await live(call, "price")
    adapter.shadow_event_begin(call.state, {"type": "x"})
    adapter.shadow_event_end(call.state)
    assert call.state.live_shadow is None
    await adapter.cancel_pending_tasks(call.state)


class _Socket:
    """Yields the given events, then ends the stream (the shape test_pilot_8d uses for the real pump)."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.close_code = None
        self._events = list(events)

    def __aiter__(self) -> _Socket:
        return self

    async def __anext__(self) -> str:
        if self._events:
            return json.dumps(self._events.pop(0))
        self.close_code = 1000
        raise StopAsyncIteration


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...

    warning = info


async def test_the_real_pump_measures_handler_time_and_a_blocked_pump_delays_the_next_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A main-session handler that blocks (as the TTS-drain wait can) holds up the next event; a live delta handled in
    the meantime is attributed from the tracker's view of what it has processed so far - and is flagged as such."""
    _flags(monkeypatch, voice_live_shadow_ownership=True)
    call = Call()
    seen: list[str] = []

    async def handler(event: dict[str, Any], *a: Any, **k: Any) -> None:
        seen.append(event["type"])
        if event["type"] == "response.text.delta":
            await asyncio.sleep(0.25)  # stands in for `await asyncio.wait({eleven_finalize_task})`

    monkeypatch.setattr(adapter, "handle_openai_event", handler)
    shadow = adapter._shadow(call.state)
    pump = asyncio.create_task(
        bridge._pump_openai_events(
            _Socket(
                [{"type": "response.text.delta"}, {"type": "input_audio_buffer.speech_started"}]
            ),
            call.phone,
            call.state,
            _Log(),
            time.monotonic(),
        )
    )
    await asyncio.sleep(
        0.1
    )  # the pump is inside the blocked handler; speech_started is already buffered unread
    assert seen == ["response.text.delta"]
    d = shadow.on_delta(
        5, item_id="ONE", legacy_accepted=True
    )  # a live delta handled in the meantime
    assert d.main_inflight_ms is not None and d.main_inflight_ms >= 50
    assert d.kind == "unassignable"  # the speech_started it belongs to has not been processed yet
    await pump
    assert seen == ["response.text.delta", "input_audio_buffer.speech_started"]
    s = shadow.summary()
    assert (
        s["main_handler_text_delta_slow_count"] == 1 and s["deltas_during_slow_main_handler"] == 1
    )
    assert s["main_reads_after_slow_handler_without_wait"] == 1
