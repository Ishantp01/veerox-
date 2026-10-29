"""Live partial transcripts belong to the caller turn that was current when their segment first appeared, and the
commit-time cut only acts on evidence that belongs to the turn it was committed for. Mocked events only."""

from __future__ import annotations

import asyncio
import base64
import math
import time
import types
from typing import Any

import pytest

from apps.api.channels.voice import adapter, local_vad, turn_controller
from apps.api.config import settings
from apps.api.tests.test_voice_barge_in_turns import _agent_speaking, _timed_call
from apps.api.tests.test_voice_end_of_turn_latency import (  # noqa: F401  (offline_judge is autouse)
    _caller_turn_over_busy_agent,
    _long_answer_still_playing,
    offline_judge,
)
from apps.api.tests.test_voice_interruption import Call, _flags, base  # noqa: F401


async def delta(call: Call, text: str, item_id: str | None = None) -> None:
    await adapter.handle_live_delta(text, call.phone, call.agent, call.state, call.log, item_id=item_id)  # type: ignore[arg-type]


async def finish_turn(call: Call, commit_id: str) -> None:
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id=commit_id)


# --- 1. an old partial arriving after a new turn started -----------------------------------------------------------


async def test_the_late_tail_of_the_previous_turn_is_not_added_to_the_new_turn() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "mujhe price", "live_A")
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")  # turn 2 begins
    assert call.state.utterance_text == ""
    await delta(call, " batao ji", "live_A")  # the live transcript was behind: turn 1's tail lands now
    assert call.state.utterance_text == ""
    await delta(call, "kya timing hai", "live_B")
    assert call.state.utterance_text == "kya timing hai"
    await adapter.cancel_pending_tasks(call.state)


# --- 2. old and new events interleaved -------------------------------------------------------------------------------


async def test_interleaved_old_and_new_fragments_keep_only_the_new_turns_words() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "pehla sawaal", "live_A")
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")
    for text, item in (("hai", "live_A"), ("dusra", "live_B"), (" jaldi", "live_A"), (" sawaal", "live_B")):
        await delta(call, text, item)
    assert call.state.utterance_text == "dusra sawaal"
    await adapter.cancel_pending_tasks(call.state)


async def test_final_transcripts_arriving_out_of_order_never_replace_the_newer_question() -> None:
    call = Call()
    for qid in ("q1", "q2"):
        await call.event("input_audio_buffer.speech_started")
        await finish_turn(call, qid)
    await call.event("conversation.item.input_audio_transcription.completed", item_id="q2", transcript="fees kitni hai")
    await call.event("conversation.item.input_audio_transcription.completed", item_id="q1", transcript="timing kya hai")
    assert call.state.pending_user_transcript == "fees kitni hai"
    assert adapter._current_transcript(call.state) == "fees kitni hai"
    await adapter.cancel_pending_tasks(call.state)


# --- the commit-time classifier only sees the committed turn's own words ----------------------------------------------


async def test_a_stale_question_tail_cannot_turn_a_new_acknowledgement_into_an_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "mujhe franchise ka price batao", "live_A")
    await finish_turn(call, "c1")
    await _long_answer_still_playing(call)

    await call.event("input_audio_buffer.speech_started")  # turn 2: a nod over the agent
    call.state.t_speech_started = time.monotonic() - 0.6
    await delta(call, " aur investment bhi", "live_A")  # stale tail of turn 1, real-looking words
    await delta(call, "haan ji", "live_B")
    await finish_turn(call, "c2")
    await asyncio.sleep(0.15)
    assert call.state.utterance_text == "haan ji"
    assert not call.cleared() and call.state.eleven_session is not None
    await adapter.cancel_pending_tasks(call.state)


async def test_a_stale_acknowledgement_cannot_hide_a_real_question() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "haan ji", "live_A")
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")
    await delta(call, " theek hai", "live_A")
    await delta(call, "mujhe fees batao", "live_B")
    assert turn_controller.classify_partial(call.state.utterance_text) == "real"
    assert call.state.utterance_text == "mujhe fees batao"
    await adapter.cancel_pending_tasks(call.state)


async def test_a_commit_handled_after_the_caller_started_a_newer_turn_does_not_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 3.0
    await finish_turn(call, "c1")
    turn_of_commit = call.state.live_turn
    await call.event("input_audio_buffer.speech_started")  # the next turn begins before the cut task runs
    await asyncio.sleep(0.1)
    assert call.state.live_turn == turn_of_commit + 1
    assert not call.cleared()  # the commit did not act on the newer turn's state
    await adapter.cancel_pending_tasks(call.state)


# --- turn boundaries --------------------------------------------------------------------------------------------------------


async def test_a_repeated_speech_started_inside_one_turn_is_not_a_new_turn() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    first = call.state.live_turn
    await delta(call, "mera budget", "live_A")
    await call.event("input_audio_buffer.speech_started")  # the VAD fires again; nothing was committed in between
    assert call.state.live_turn == first
    await delta(call, " paanch lakh hai", "live_A")
    assert call.state.utterance_text.endswith("paanch lakh hai")
    await adapter.cancel_pending_tasks(call.state)


async def test_fragments_without_a_segment_id_are_accepted_as_before() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "price", None)
    await delta(call, " batao", None)
    assert call.state.utterance_text == "price batao"
    await adapter.cancel_pending_tasks(call.state)


async def test_one_provider_segment_continuing_across_turns_is_adopted_after_a_long_silence_of_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter, "LIVE_REBIND_AFTER_SECONDS", 0.05)
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "pehla", "live_ONE")
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")
    await delta(call, " turant", "live_ONE")
    assert call.state.utterance_text == ""  # too soon: treated as the old turn's tail
    await asyncio.sleep(0.1)
    await delta(call, " dusra sawaal", "live_ONE")  # still the only segment and the new turn has no text: adopt
    assert call.state.utterance_text == " dusra sawaal"
    await adapter.cancel_pending_tasks(call.state)


# --- an old response finishing after the new turn began ---------------------------------------------------------------


async def test_an_old_response_completing_after_the_new_turn_began_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True))
    await _agent_speaking(call, "resp_1")
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 3.0
    await finish_turn(call, "c2")
    await call.event("input_audio_buffer.speech_started")  # and they start a third utterance
    await delta(call, "teesra sawaal", "live_C")
    await call.event("response.done", response={"id": "resp_1", "status": "completed"})  # resp_1 ends only now
    await call.event("response.text.delta", delta="PURANA", response_id="resp_1")
    assert call.state.utterance_text == "teesra sawaal"
    assert "PURANA" not in "".join(s.sent[0] for s in [call.state.eleven_session] if s is not None and s.sent)
    await adapter.cancel_pending_tasks(call.state)


# --- 3. noise and side talk versus the commit-time threshold -----------------------------------------------------------


class Vad:
    """The local VAD's per-turn evidence, set by the test."""

    def __init__(self, confident: bool) -> None:
        self.confident = confident
        self.marked = 0

    def utterance_confident(self, seconds: float) -> bool:
        return self.confident

    def mark_utterance(self) -> None:
        self.marked += 1

    def sustained(self, seconds: float) -> bool:
        return False


async def test_without_the_vad_only_a_longer_turn_than_before_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    for seconds, expect_cut in ((1.6, False), (1.9, False), (2.2, True)):
        call = _timed_call(live=types.SimpleNamespace(healthy=True))
        await _long_answer_still_playing(call)
        await _caller_turn_over_busy_agent(call, text="", speech_seconds=seconds)
        assert call.cleared() is expect_cut, seconds
        await adapter.cancel_pending_tasks(call.state)


async def test_soft_side_talk_is_not_a_turn_but_confident_speech_is(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    for confident, expect_cut in ((False, False), (True, True)):
        call = _timed_call(live=types.SimpleNamespace(healthy=True), vad=Vad(confident))
        await _long_answer_still_playing(call)
        await _caller_turn_over_busy_agent(call, text="", speech_seconds=1.8)
        assert call.cleared() is expect_cut, confident
        await adapter.cancel_pending_tasks(call.state)


async def test_confident_loud_speech_that_is_only_an_acknowledgement_still_does_not_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True), vad=Vad(True))
    await call.event("input_audio_buffer.speech_started")
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 2.5
    await delta(call, "haan haan theek hai", "live_X")
    await finish_turn(call, "c1")
    await asyncio.sleep(0.15)
    assert not call.cleared()
    await adapter.cancel_pending_tasks(call.state)


async def test_a_genuine_interruption_with_text_is_cut_whatever_the_vad_says(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_commit_time_cut=True)
    call = _timed_call(live=types.SimpleNamespace(healthy=True), vad=Vad(False))
    await _long_answer_still_playing(call)
    await call.event("input_audio_buffer.speech_started")
    call.state.t_speech_started = time.monotonic() - 1.2
    await delta(call, "mujhe franchise ka price batao", "live_X")
    await finish_turn(call, "c1")
    await asyncio.sleep(0.15)
    assert call.cleared()
    await adapter.cancel_pending_tasks(call.state)


# --- the local VAD's per-turn evidence ------------------------------------------------------------------------------------


def _ulaw(sample: int) -> int:
    bias, clip = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), clip) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _tone(amplitude: int, seconds: float) -> str:
    n = int(8000 * seconds)
    data = bytes(_ulaw(int(amplitude * math.sin(2 * math.pi * 300 * i / 8000))) for i in range(n))
    return base64.b64encode(data).decode()


def test_the_vad_counts_confident_speech_per_turn_and_resets_between_turns() -> None:
    vad = local_vad.LocalVAD()
    vad.feed(_tone(0, 0.5))  # quiet lead-in sets the noise floor
    vad.feed(_tone(9000, 1.2))
    vad.feed(_tone(0, 0.1))  # a quiet gap resets the unbroken run but not the turn's evidence
    vad.feed(_tone(9000, 0.4))
    assert vad.utt_seconds > 1.2 and vad.utterance_confident(1.0)
    assert not vad.sustained(1.5)  # the unbroken-run gate is a different, stricter question
    vad.mark_utterance()
    assert vad.utt_seconds == 0.0 and not vad.utterance_confident(0.2)


def test_near_silence_and_short_blips_never_reach_the_confidence_bar() -> None:
    """What the energy gate CAN screen out: sound below its absolute floor, and blips too short to count. It cannot
    tell quiet speech or steady background noise from the caller (no spectral model), so side talk louder than
    the floor passes: that residual risk is covered by the transcript check and the benchmark, not by this gate."""
    quiet = local_vad.LocalVAD()
    quiet.feed(_tone(0, 0.5))
    quiet.feed(_tone(70, 3.0))
    assert not quiet.utterance_confident(1.0)
    blip = local_vad.LocalVAD()
    blip.feed(_tone(0, 0.5))
    blip.feed(_tone(9000, 0.3))  # a cough or click
    assert not blip.utterance_confident(1.0)


def test_a_new_live_turn_resets_the_vads_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    state = adapter.CallState(user_id=None, conversation_id=None, org_id=None, base_instructions="B")  # type: ignore[arg-type]
    state.vad = Vad(True)
    adapter._begin_live_turn(state)
    adapter._begin_live_turn(state)  # no commit in between: same turn
    assert state.live_turn == 1 and state.vad.marked == 1  # type: ignore[attr-defined]
    state.utt_seq = 1
    adapter._begin_live_turn(state)
    assert state.live_turn == 2 and state.vad.marked == 2  # type: ignore[attr-defined]


# --- documented limitations: cases where stale text CAN still be adopted (asserting today's behaviour) ----------------
# These pass on purpose. They pin down what the ownership rule cannot do, so nobody mistakes it for a guarantee.


async def test_limitation_a_segment_whose_first_fragment_arrives_after_the_next_turn_began_is_adopted_by_it() -> None:
    """Live lag longer than the gap between two utterances: nothing on the fragment says which turn it was spoken in."""
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")  # turn 1: the live transcriber has produced nothing yet
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")  # turn 2 starts before turn 1's first fragment arrives
    await delta(call, "pehle sawaal ke shabd", "live_A")  # turn 1's words, first seen now
    assert call.state.utterance_text == "pehle sawaal ke shabd"  # attributed to turn 2
    await adapter.cancel_pending_tasks(call.state)


async def test_limitation_a_late_tail_is_adopted_once_the_rebind_window_has_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "LIVE_REBIND_AFTER_SECONDS", 0.05)
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "pehla", "live_A")
    await finish_turn(call, "c1")
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.1)  # the new turn has spoken a while with no text of its own...
    await delta(call, " purani baat", "live_A")  # ...so a genuinely old tail looks like a continuing segment
    assert call.state.utterance_text == " purani baat"
    await adapter.cancel_pending_tasks(call.state)


async def test_limitation_a_commit_that_never_arrives_leaves_two_utterances_in_one_turn() -> None:
    call = Call(live=types.SimpleNamespace(healthy=True))
    await call.event("input_audio_buffer.speech_started")
    await delta(call, "pehla", "live_A")
    await call.event("input_audio_buffer.speech_stopped")  # no committed event follows
    await call.event("input_audio_buffer.speech_started")
    await delta(call, " tail", "live_A")
    assert call.state.live_turn == 1 and call.state.utterance_text.endswith("tail")
    await adapter.cancel_pending_tasks(call.state)
