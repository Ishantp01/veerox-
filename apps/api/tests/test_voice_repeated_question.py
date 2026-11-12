"""A caller who repeats the same (or a similar) question is always answered again, never silently dropped.

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


@pytest.mark.parametrize("again", [Q + "?", "aapki fees kitni hai batao"])
async def test_repeat_while_answering_is_still_answered(again: str) -> None:
    call = await _first_answer_in_flight()
    await _ask(call, "i2", again)
    assert call.state.reply_pending is True


async def test_a_different_question_is_answered() -> None:
    call = await _first_answer_in_flight()
    await _ask(call, "i2", "demo kab milega mujhe")
    assert call.state.reply_pending is True


async def _fire(call: Call) -> str:
    call.state.reply_pending = True
    call.state.response_active = False
    await adapter._fire_pending_reply(call.agent, call.state)
    return (call.replies()[-1].get("response") or {}).get("instructions", "")


async def test_repeat_guard_is_off_by_default() -> None:
    call = Call()
    await _fire(call)
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)


async def test_repeat_guard_when_enabled_applies_only_to_a_quick_second_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter.settings, "voice_repeat_guard", True)
    call = Call()
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)
    assert adapter.REPEAT_GUARD_INSTRUCTION in await _fire(call)
    call.state.last_reply_at = time.monotonic() - adapter.REPEAT_GUARD_WINDOW_SECONDS - 1
    assert adapter.REPEAT_GUARD_INSTRUCTION not in await _fire(call)
