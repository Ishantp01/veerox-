"""A caller who repeats the same question straight away gets one answer, not two.

Driven through the real adapter with fake sockets (see test_voice_interruption)."""

from __future__ import annotations

import time

import pytest

from apps.api.channels.voice import adapter
from apps.api.tests.test_voice_interruption import Call, base  # noqa: F401 - base is an autouse fixture

Q = "aapki fees kitni hai"


async def _ask(call: Call, item: str, text: str) -> None:
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id=item)
    await call.event("conversation.item.input_audio_transcription.completed", item_id=item, transcript=text)


async def _first_answer_in_flight(text: str = Q) -> Call:
    call = Call()
    await _ask(call, "i1", text)
    call.state.reply_pending = False
    call.state.answering_utt = 1
    await call.agent_starts_answering(question=text)
    return call


async def test_same_question_again_while_answering_gets_no_second_reply() -> None:
    call = await _first_answer_in_flight()
    await _ask(call, "i2", Q + "?")
    assert call.state.reply_pending is False
    assert call.replies() == []


async def test_slightly_different_wording_still_counts_as_the_same_question() -> None:
    call = await _first_answer_in_flight()
    await _ask(call, "i2", "aapki fees kitni hai batao")
    assert call.state.reply_pending is False


async def test_a_different_question_is_still_answered() -> None:
    call = await _first_answer_in_flight()
    await _ask(call, "i2", "demo kab milega mujhe")
    assert call.state.reply_pending is True


async def test_repeat_after_the_answer_has_finished_is_answered_again() -> None:
    call = await _first_answer_in_flight()
    call.state.response_active = False
    call.state.playback_end = 0.0
    await _ask(call, "i2", Q)
    assert call.state.reply_pending is True


async def test_repeat_is_answered_when_the_first_answer_was_cut() -> None:
    call = await _first_answer_in_flight()
    call.state.cut_at = time.monotonic() + 0.01  # the first answer was stopped after the question was heard
    call.state.suppress_response_audio = True
    await _ask(call, "i2", Q)
    assert call.state.reply_pending is True


async def test_old_repeat_outside_the_window_is_answered() -> None:
    call = await _first_answer_in_flight()
    call.state.last_turn_at = time.monotonic() - adapter.REPEAT_QUESTION_WINDOW_SECONDS - 1
    await _ask(call, "i2", Q)
    assert call.state.reply_pending is True


@pytest.mark.parametrize("text", ["haan", "theek hai", "ok ji"])
async def test_short_replies_are_never_treated_as_repeats(text: str) -> None:
    call = await _first_answer_in_flight(text)
    assert adapter._is_repeated_question(call.state, text) is False


async def _fire(call: Call) -> str:
    call.state.reply_pending = True
    call.state.response_active = False
    await adapter._fire_pending_reply(call.agent, call.state)
    return (call.replies()[-1].get("response") or {}).get("instructions", "")


async def test_first_reply_has_no_repeat_guard_but_a_quick_second_one_does() -> None:
    call = Call()
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)
    assert adapter.REPEAT_GUARD_INSTRUCTION in await _fire(call)


async def test_repeat_guard_expires_and_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    call = Call()
    await _fire(call)
    call.state.last_reply_at = time.monotonic() - adapter.REPEAT_GUARD_WINDOW_SECONDS - 1
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)
    monkeypatch.setattr(adapter.settings, "voice_repeat_guard", False)
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)
