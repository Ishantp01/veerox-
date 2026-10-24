"""Phase 7A: adaptive turn-taking, patience with silence, and turn-taking counters.

Fake sockets and synthetic transcripts only. These tests prove what our code decides from the words it is given;
they cannot show how real callers pause, or how well the live transcriber's partial text keeps up with them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from apps.api.channels.voice import adapter, latency, turn_controller
from apps.api.channels.voice.turn_controller import endpoint_hold_seconds as hold
from apps.api.config import settings
from apps.api.core.conversation.silence import SilenceMonitor
from apps.api.tests.test_voice_interruption import Call, _flags, base  # noqa: F401  (autouse fixture)

# --- end-of-turn detection --------------------------------------------------------------------------------------------------------

COMPLETE = [
    "Haan, mujhe franchise ke baare mein jaanna hai.",
    "Investment kitna lagega?",
    "Mera budget paanch lakh hai",
    "What is the expected return?",
    "Mujhe abhi decide nahi karna.",
    "Agar price kya hai",
]
UNFINISHED = [
    "Mera budget",
    "agar main investment karun",
    "franchise ke baare mein",
    "Achha, agar main investment karun toh...",
    "haan mera matlab",
    "shayad paanch lakh ke",
]
ASKS_FOR_TIME = ["ek minute", "Mera budget... ek minute", "ek second ji", "let me think", "one second please", "ruko zara ruko", "soch raha hoon"]


@pytest.mark.parametrize("text", COMPLETE)
def test_a_finished_statement_is_answered_at_once(text: str) -> None:
    assert hold(text, False, smart=True) == 0.0


@pytest.mark.parametrize("text", UNFINISHED)
def test_an_unfinished_sentence_is_waited_for_in_smart_mode(text: str) -> None:
    assert hold(text, False, smart=True) >= turn_controller.HOLD_CONTINUATION_SECONDS


@pytest.mark.parametrize("text", ASKS_FOR_TIME)
def test_a_request_for_a_moment_gets_a_long_wait(text: str) -> None:
    assert hold(text, False, smart=True) == turn_controller.HOLD_THINKING_SECONDS


def test_the_long_wait_ends_as_soon_as_the_caller_says_what_they_were_thinking() -> None:
    assert hold("ek minute shayad paanch lakh ke aas-paas hai", False, smart=True) == 0.0
    assert hold("ek minute... mera budget paanch lakh hai.", False, smart=True) == 0.0


@pytest.mark.parametrize("text", UNFINISHED[:3] + ASKS_FOR_TIME)
def test_without_smart_mode_none_of_the_new_rules_apply(text: str) -> None:
    legacy = hold(text, False)
    assert legacy in (0.0, turn_controller.HOLD_CONTINUATION_SECONDS, turn_controller.HOLD_LONE_FILLER_SECONDS)
    assert legacy != turn_controller.HOLD_THINKING_SECONDS


def test_words_that_merely_contain_a_waiting_cue_are_not_a_request_for_time() -> None:
    for text in ("weight loss ke baare mein batao", "waiter ko bulao", "mujhe ek minute ka plan chahiye"):
        assert not turn_controller.thinking_cue(text), text


async def _commit(call: Call, text: str) -> None:
    call.state.live = type("Live", (), {"healthy": True})()
    call.state.utterance_text = text
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")


async def test_a_thinking_pause_is_not_answered_and_the_answer_comes_after_they_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    call = Call()
    await _commit(call, "Mera budget... ek minute")
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 0  # the agent does not talk over someone who is thinking
    await adapter.handle_live_delta(" shayad paanch lakh ke aas-paas hai.", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1  # one answer, to the finished thought


async def test_the_same_pause_gets_an_immediate_answer_without_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=False)
    call = Call()
    await _commit(call, "Mera budget... ek minute")
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1  # legacy behaviour is untouched


async def test_a_pause_that_never_resumes_is_still_answered_after_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    monkeypatch.setattr(turn_controller, "HOLD_THINKING_SECONDS", 0.15)
    call = Call()
    await _commit(call, "ek minute")
    await asyncio.sleep(0.05)
    assert len(call.replies()) == 0
    await asyncio.sleep(0.3)
    assert len(call.replies()) == 1  # the caller is never left waiting forever


async def test_the_caller_resuming_to_speak_cancels_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    monkeypatch.setattr(turn_controller, "HOLD_THINKING_SECONDS", 0.2)
    call = Call()
    await _commit(call, "ek minute")
    await call.event("input_audio_buffer.speech_started")  # they carry on
    await asyncio.sleep(0.4)
    assert len(call.replies()) == 0  # nothing fires while they are talking


# --- silence ------------------------------------------------------------------------------------------------------------------------


def test_a_caller_who_asked_for_time_gets_a_longer_first_silence() -> None:
    monitor = SilenceMonitor(check_after=8.0)
    monitor.agent_finished(100.0)
    assert monitor.poll(108.0, busy=False) == "check"
    patient = SilenceMonitor(check_after=8.0)
    patient.agent_finished(100.0, patience=2.5)
    assert patient.poll(108.0, busy=False) is None and patient.poll(119.9, busy=False) is None
    assert patient.poll(120.0, busy=False) == "check"


def test_patience_applies_only_to_the_first_check() -> None:
    monitor = SilenceMonitor(check_after=8.0)
    monitor.agent_finished(0.0, patience=2.5)
    assert monitor.poll(20.0, busy=False) == "check"
    monitor.agent_finished(20.0, patience=2.5)  # after a check the normal interval resumes
    assert monitor.poll(28.0, busy=False) == "check"


def test_the_second_check_is_not_a_repeat_of_the_first() -> None:
    first, again = adapter._SILENCE_CHECK_REQUEST, adapter._SILENCE_CHECK_AGAIN_REQUEST
    assert first != again and "main yahin hoon" in first and "Never mention a connection" in first
    assert "Do NOT say hello" in again and "do NOT ask whether they can hear you" in again


async def test_the_caller_asking_for_time_is_remembered_for_the_silence_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    call = Call()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="ek minute, soch raha hoon")
    assert call.state.caller_thinking is True
    await call.event("conversation.item.input_audio_transcription.completed", transcript="paanch lakh")
    assert call.state.caller_thinking is False


# --- turn-taking counters ---------------------------------------------------------------------------------------------------------


def _capture(call: Call) -> list[str]:
    seen: list[str] = []
    call.state.latency.event = lambda kind, **extra: seen.append(kind)  # type: ignore[method-assign]
    return seen


async def test_stopping_for_an_acknowledgement_is_counted_as_a_false_interruption() -> None:
    call = Call()
    seen = _capture(call)
    call.state.cut_at = time.monotonic()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="haan ji")
    assert seen == ["false_interruption"] and call.state.cut_at == 0.0  # counted once


async def test_stopping_for_a_real_question_is_not_a_false_interruption() -> None:
    call = Call()
    seen = _capture(call)
    call.state.cut_at = time.monotonic()
    await call.event("conversation.item.input_audio_transcription.completed", transcript="price kya hai")
    assert "false_interruption" not in seen


async def test_an_old_cut_is_not_blamed_on_a_later_acknowledgement() -> None:
    call = Call()
    seen = _capture(call)
    call.state.cut_at = time.monotonic() - 30
    await call.event("conversation.item.input_audio_transcription.completed", transcript="haan")
    assert seen == []


async def test_speaking_again_before_any_of_our_answer_is_heard_is_a_suspected_premature_response() -> None:
    call = Call()
    seen = _capture(call)
    call.state.response_active, call.state.t_reply_sent, call.state.resp_audio_seconds = True, time.monotonic(), 0.0
    await call.event("input_audio_buffer.speech_started")
    assert seen == ["premature_response_suspected"]


async def test_speaking_over_an_answer_that_is_playing_is_not_premature() -> None:
    call = Call()
    seen = _capture(call)
    call.state.response_active, call.state.t_reply_sent, call.state.resp_audio_seconds = True, time.monotonic() - 5, 2.0
    await call.event("input_audio_buffer.speech_started")
    assert "premature_response_suspected" not in seen


async def test_stale_and_muted_audio_is_counted_and_never_forwarded() -> None:
    call = Call()
    seen = _capture(call)
    call.state.cancelled_response_ids = {"old"}
    await call.event("response.output_audio.delta", response_id="old", delta="AAAA")
    call.state.suppress_response_audio = True
    await call.event("response.output_audio.delta", response_id="new", delta="BBBB")
    assert seen == ["stale_event_dropped", "muted_audio_dropped"] and call.audio() == []


def test_counts_and_rates_per_100_turns_come_from_the_log_events() -> None:
    events = [{"event": "voice_turn_latency"}] * 50 + [
        {"event": "voice_turn_event", "kind": "false_interruption"},
        {"event": "voice_turn_event", "kind": "false_interruption"},
        {"event": "voice_turn_event", "kind": "premature_response_suspected"},
        {"event": "voice_turn_event"},  # malformed: ignored
    ]
    result = latency.turn_event_counts(events)
    assert result["turns"] == 50 and result["counts"] == {"false_interruption": 2, "premature_response_suspected": 1}
    assert result["per_100_turns"]["false_interruption"] == 4.0
    assert latency.turn_event_counts([])["per_100_turns"] == {}


def test_the_event_helper_never_raises_and_is_silent_when_tracking_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_latency_tracking", False)
    latency.LatencyTracker(call_uuid="c").event("false_interruption")  # no error, nothing logged
    monkeypatch.setattr(settings, "voice_latency_tracking", True)
    latency.LatencyTracker(call_uuid="c").event("false_interruption", anything=object())


# --- the existing safety net still holds -------------------------------------------------------------------------------------------


def test_every_new_behaviour_is_behind_the_existing_smart_turn_flag() -> None:
    assert settings.voice_smart_turn_taking is False  # default: all of the above is off
    assert hold("ek minute", False) != turn_controller.HOLD_THINKING_SECONDS
