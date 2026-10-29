"""Bug C: delay between the caller finishing and the agent answering.

Mocked provider events only. What these measure is the time OUR code holds a committed turn before sending
``response.create``; OpenAI, ElevenLabs and network time are not simulated (see the production log analysis in
the report). The baseline-vs-fixed test prints both numbers.
"""

from __future__ import annotations

import asyncio
import time
import types
from typing import Any

import pytest

from apps.api.channels.voice import adapter, realtime_bridge, turn_controller
from apps.api.config import settings
from apps.api.tests.test_voice_barge_in_turns import TimedPhone, _agent_speaking, _timed_call
from apps.api.tests.test_voice_interruption import Call, _flags, base  # noqa: F401  (base is an autouse fixture)

REMAINING_AUDIO_SECONDS = 6.0


@pytest.fixture(autouse=True)
def offline_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The live LLM turn judge would call OpenAI (and stall the event loop on DNS here): none of these tests may."""

    async def no_judgement(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(turn_controller, "judge_turn", no_judgement)


LIVE_TEXT_LAG_SECONDS = 0.3  # stands in for the live transcript arriving after the caller finished (3-4 s in production)


async def _caller_turn_over_busy_agent(call: Call, *, text: str, speech_seconds: float) -> float:
    """The caller speaks over a long answer and finishes; the live text for it arrives LIVE_TEXT_LAG_SECONDS after
    the commit. Returns how long after the commit `response.create` was sent (inf: not within 0.6 s)."""
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - speech_seconds
    await call.event("input_audio_buffer.speech_stopped")
    committed = time.monotonic()
    await call.event("input_audio_buffer.committed", item_id=f"item_{time.monotonic()}")
    delivered = not text
    for _ in range(60):
        await asyncio.sleep(0.01)
        if not delivered and time.monotonic() - committed >= LIVE_TEXT_LAG_SECONDS:
            delivered = True
            await adapter.handle_live_delta(text, call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
        if call.replies():
            return time.monotonic() - committed
    return float("inf")


async def _long_answer_still_playing(call: Call) -> None:
    """The model finished generating its answer long ago (response.done); its audio is still queued at the
    provider. This is the production situation in which the reply waited for the audio to play out."""
    await _agent_speaking(call)
    await call.event("response.done", response={"id": "resp_1", "status": "completed"})
    call.state.playback_end = time.monotonic() + REMAINING_AUDIO_SECONDS


async def test_baseline_vs_fixed_reply_delay_for_a_real_question_over_a_long_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results: dict[str, float] = {}
    for label, flag in (("baseline (flag off)", False), ("fixed (VOICE_COMMIT_TIME_CUT)", True)):
        _flags(monkeypatch, voice_commit_time_cut=flag)
        call = _timed_call(live=types.SimpleNamespace(healthy=True))
        await _long_answer_still_playing(call)
        results[label] = await _caller_turn_over_busy_agent(call, text="mujhe franchise ka price batao", speech_seconds=2.5)
        await adapter.cancel_pending_tasks(call.state)
    with capsys.disabled():
        print("\n[mocked] commit -> response.create while agent had 6.0 s of audio left:")
        for label, value in results.items():
            shown = "not sent within 0.6 s (waits for the audio to play out, ~6 s)" if value == float("inf") else f"{value * 1000:.0f} ms"
            print(f"   {label}: {shown}")
    assert results["baseline (flag off)"] > 0.25  # held until the (late) live text produced a verdict
    assert results["fixed (VOICE_COMMIT_TIME_CUT)"] < 0.25


async def test_a_long_utterance_with_no_live_text_yet_is_still_resolved_at_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    assert await _caller_turn_over_busy_agent(call, text="", speech_seconds=2.5) < 0.25
    await adapter.cancel_pending_tasks(call.state)


@pytest.mark.parametrize(
    ("text", "speech_seconds"),
    [("haan ji", 0.6), ("theek hai", 0.8), ("hmm achha", 0.7), ("ok ok ok", 1.0), ("haan haan theek hai ji", 1.4), ("", 0.6)],
)
async def test_filler_words_and_short_acknowledgements_are_not_resolved_as_turns(
    monkeypatch: pytest.MonkeyPatch, text: str, speech_seconds: float
) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await _caller_turn_over_busy_agent(call, text=text, speech_seconds=speech_seconds)
    assert not call.cleared() and call.state.eleven_session is not None  # the agent keeps talking
    await adapter.cancel_pending_tasks(call.state)


async def test_unclear_but_real_text_present_at_commit_is_resolved_without_waiting_for_an_llm_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_judgement(*a: Any, **k: Any) -> None:
        return None  # the LLM judge is slow/unavailable: the case the commit-time decision exists for

    monkeypatch.setattr(turn_controller, "judge_turn", no_judgement)
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 1.2
    # Four words with no question cue: "unsure" to the cheap classifier, so the live path waits on the LLM.
    await adapter.handle_live_delta("mera naam rahul hai", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    assert call.replies() == []
    await call.event("input_audio_buffer.speech_stopped")
    committed = time.monotonic()
    await call.event("input_audio_buffer.committed", item_id="i1")
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1 and time.monotonic() - committed < 0.25
    await adapter.cancel_pending_tasks(call.state)


async def test_the_flag_is_off_by_default_and_never_changes_an_idle_agents_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings.voice_commit_time_cut is False
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = Call()  # agent idle
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="i1")
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1 and not call.cleared()
    await adapter.cancel_pending_tasks(call.state)


# --- not answering before the caller is done ---------------------------------------------------------------------


async def test_no_reply_while_the_caller_is_still_speaking() -> None:
    call = Call()
    await call.event("input_audio_buffer.speech_started")
    for part in ("mujhe", " franchise", " ke"):
        await adapter.handle_live_delta(part, call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    assert call.replies() == []  # nothing is sent until the turn is committed


async def test_a_pause_inside_a_sentence_is_waited_out_and_answered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    monkeypatch.setattr(turn_controller, "HOLD_CONTINUATION_SECONDS", 0.3)
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await adapter.handle_live_delta("mera budget hai aur", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await call.event("input_audio_buffer.speech_stopped")  # the VAD saw a pause...
    await call.event("input_audio_buffer.committed", item_id="i1")  # ...and committed an unfinished sentence
    await asyncio.sleep(0.1)
    assert call.replies() == []  # held: the sentence is not finished

    await call.event("input_audio_buffer.speech_started")  # they carry on within the hold
    await adapter.handle_live_delta(" paanch lakh", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await asyncio.sleep(0.4)
    assert call.replies() == []  # the held reply was withdrawn, not fired mid-sentence
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="i2")
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1
    await adapter.cancel_pending_tasks(call.state)


# --- duplicates and ordering -------------------------------------------------------------------------------------------


async def test_redelivered_speech_end_and_commit_events_produce_one_reply() -> None:
    call = Call()
    await call.event("input_audio_buffer.speech_started")
    for _ in range(2):
        await call.event("input_audio_buffer.speech_stopped")
        await call.event("input_audio_buffer.committed", item_id="i1")
    for _ in range(2):
        await call.event("conversation.item.input_audio_transcription.completed", item_id="i1", transcript="price batao")
    await asyncio.sleep(0.15)
    assert len(call.replies()) == 1 and call.state.utt_seq == 1
    await adapter.cancel_pending_tasks(call.state)


async def test_rapid_consecutive_questions_each_get_their_own_reply_in_order() -> None:
    call = Call()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="q1")
    await asyncio.sleep(0.05)
    assert len(call.replies()) == 1 and call.state.answering_utt == 1

    # The second question is committed while the first answer is still being generated.
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="q2")
    await asyncio.sleep(0.05)
    assert len(call.replies()) == 1  # never two responses at once

    await call.event("response.created", response={"id": "r1"})
    await call.event("response.done", response={"id": "r1", "status": "completed"})
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 2 and call.state.answering_utt == 2
    await adapter.cancel_pending_tasks(call.state)


async def test_the_final_transcript_is_marked_and_the_vad_silence_is_recorded() -> None:
    call = Call()
    notes: dict[str, Any] = {}
    marks: list[str] = []
    call.state.latency.enabled = True
    original_mark = call.state.latency.mark
    call.state.latency.mark = lambda stage, **k: (marks.append(stage), original_mark(stage, **k))[1]  # type: ignore[method-assign]
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="i1")
    await call.event("conversation.item.input_audio_transcription.completed", item_id="i1", transcript="price batao")
    assert "transcript_final" in marks
    turn = call.state.latency._turn
    assert turn is not None and turn.extra.get("vad_silence_ms") == settings.voice_vad_silence_ms
    await asyncio.sleep(0.05)
    await adapter.cancel_pending_tasks(call.state)


@pytest.mark.parametrize("silence_ms", [800, 900, 1000, 1100])
def test_the_vad_silence_setting_reaches_the_realtime_session(monkeypatch: pytest.MonkeyPatch, silence_ms: int) -> None:
    monkeypatch.setattr(settings, "voice_vad_silence_ms", silence_ms)
    monkeypatch.setattr(settings, "voice_adaptive_endpointing", False)
    event = realtime_bridge._session_update_event("x")
    detection = event["session"]["audio"]["input"]["turn_detection"]
    assert detection["silence_duration_ms"] == silence_ms and detection["type"] == "server_vad"
    assert detection["create_response"] is False  # we decide when to answer; the server never auto-replies


# --- review follow-ups: noise, pauses, duplicates and late events around a commit-time cut -----------------------


@pytest.mark.parametrize("noise", ["aaaa", "mmmm", "...", "hmmmm"])
@pytest.mark.parametrize("smart", [False, True])
async def test_background_noise_text_present_at_commit_never_triggers_a_commit_time_cut(
    monkeypatch: pytest.MonkeyPatch, noise: str, smart: bool
) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True, voice_smart_turn_taking=smart)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 2.0
    await adapter.handle_live_delta(noise, call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="n1")
    await asyncio.sleep(0.15)
    assert not call.cleared() and call.state.eleven_session is not None
    await adapter.cancel_pending_tasks(call.state)


async def test_a_duplicate_commit_over_a_busy_agent_cuts_once_and_replies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 2.5
    await call.event("input_audio_buffer.speech_stopped")
    for _ in range(3):  # the same commit delivered three times
        await call.event("input_audio_buffer.committed", item_id="same")
    await asyncio.sleep(0.2)
    assert len(call.replies()) == 1 and call.state.utt_seq == 1
    assert [m for m in call.phone.sent if m.get("event") == "clearAudio"]  # cut happened
    await adapter.cancel_pending_tasks(call.state)


async def test_late_events_of_the_cut_answer_cannot_touch_the_new_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _agent_speaking(call, "resp_1")  # still generating when the caller commits their turn
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 2.5
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="q2")
    await asyncio.sleep(0.1)
    assert "resp_1" in call.state.superseded_response_ids and call.replies() == []  # waits for resp_1's own end

    await call.event("response.done", response={"id": "resp_1", "status": "completed"})
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1
    await call.event("response.created", response={"id": "resp_2"})
    await call.event("response.text.delta", delta="Naya jawab", response_id="resp_2")
    session = call.state.eleven_session
    await call.event("response.text.delta", delta="PURANA", response_id="resp_1")  # a straggler from the cut answer
    await call.event("response.done", response={"id": "resp_1", "status": "completed"})
    assert call.state.response_id == "resp_2" and call.state.response_active
    assert call.state.eleven_session is session and session is not None and session.sent == ["Naya jawab"]
    await adapter.cancel_pending_tasks(call.state)


async def test_an_unfinished_sentence_over_a_busy_agent_is_held_before_it_is_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller took the floor (so the agent stops), but a sentence that trails off is not answered mid-thought."""
    _flags(monkeypatch, voice_commit_time_cut=True, voice_smart_turn_taking=True)
    monkeypatch.setattr(turn_controller, "HOLD_CONTINUATION_SECONDS", 0.4)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 2.0
    await adapter.handle_live_delta("mera budget hai aur", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="i1")
    await asyncio.sleep(0.15)
    assert call.cleared() and call.replies() == []  # agent stopped; reply held for the rest of the sentence
    await call.event("input_audio_buffer.speech_started")  # they carry on
    await asyncio.sleep(0.5)
    assert call.replies() == []
    await adapter.cancel_pending_tasks(call.state)


async def test_a_failure_inside_the_early_cut_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)

    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("provider socket gone")

    monkeypatch.setattr(adapter, "_cut_now", boom)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await _caller_turn_over_busy_agent(call, text="", speech_seconds=2.5)
    assert "voice_commit_time_cut_failed" in call.log.warnings
    await adapter.cancel_pending_tasks(call.state)
