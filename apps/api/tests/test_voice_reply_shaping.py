"""Short replies and the bounded wait for the caller's own transcript (VOICE_REPLY_BREVITY /
VOICE_REPLY_WAIT_TRANSCRIPT_MS), found from a real call where 28-second replies were talked over and a
reply fired before the transcript existed ("products business" answered as "travel business")."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from apps.api.channels.voice import adapter
from apps.api.config import settings

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def _state(**kw: Any) -> adapter.CallState:
    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE PROMPT"
    )
    state.reply_pending = True
    for key, value in kw.items():
        setattr(state, key, value)
    return state


async def test_defaults_are_short_replies_on_and_transcript_wait_off() -> None:
    from apps.api.config import Settings

    assert Settings.model_fields["voice_reply_brevity"].default is True
    assert Settings.model_fields["voice_reply_wait_transcript_ms"].default == 0


async def test_a_normal_reply_carries_the_short_reply_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_brevity", True)
    ws, state = _Socket(), _state()
    await adapter._fire_pending_reply(ws, state)
    instructions = ws.sent[0]["response"]["instructions"]
    assert instructions.startswith("BASE PROMPT") and adapter.BREVITY_INSTRUCTION in instructions


async def test_the_short_reply_rule_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_brevity", False)
    ws, state = _Socket(), _state()
    await adapter._fire_pending_reply(ws, state)
    assert ws.sent == [{"type": "response.create"}]


async def test_without_the_wait_a_reply_fires_even_with_a_transcript_outstanding() -> None:
    ws, state = _Socket(), _state(transcripts_outstanding=1)
    await adapter._fire_pending_reply(ws, state)
    assert len(ws.sent) == 1


async def test_the_reply_waits_for_the_transcript_then_fires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_wait_transcript_ms", 2000)
    ws, state = _Socket(), _state(transcripts_outstanding=1)
    task = asyncio.create_task(adapter._fire_pending_reply(ws, state))
    await asyncio.sleep(0.2)
    assert ws.sent == [] and not task.done()  # still waiting for the words
    state.transcripts_outstanding = 0
    await asyncio.wait_for(task, 1.0)
    assert len(ws.sent) == 1 and state.reply_pending is False


async def test_the_wait_is_bounded_when_the_transcript_never_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_wait_transcript_ms", 150)
    ws, state = _Socket(), _state(transcripts_outstanding=1)
    await asyncio.wait_for(adapter._fire_pending_reply(ws, state), 2.0)
    assert len(ws.sent) == 1  # gave up waiting and answered, as before


async def test_no_wait_when_the_transcript_is_already_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_wait_transcript_ms", 5000)
    ws, state = _Socket(), _state(transcripts_outstanding=0)
    await asyncio.wait_for(adapter._fire_pending_reply(ws, state), 0.5)
    assert len(ws.sent) == 1


async def test_a_caller_who_starts_speaking_again_cancels_the_pending_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "voice_reply_wait_transcript_ms", 2000)
    ws, state = _Socket(), _state(transcripts_outstanding=1)
    task = asyncio.create_task(adapter._fire_pending_reply(ws, state))
    await asyncio.sleep(0.1)
    state.caller_speaking = True
    await asyncio.wait_for(task, 1.0)
    assert ws.sent == [] and state.reply_pending is True  # re-scheduled by their next commit


async def test_a_reply_sent_by_someone_else_while_waiting_is_not_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_reply_wait_transcript_ms", 2000)
    ws, state = _Socket(), _state(transcripts_outstanding=1)
    task = asyncio.create_task(adapter._fire_pending_reply(ws, state))
    await asyncio.sleep(0.1)
    state.response_active = True
    state.transcripts_outstanding = 0
    await asyncio.wait_for(task, 1.0)
    assert ws.sent == []


# --- VOICE_LATE_TRANSCRIPT_CUT: words that arrive after a short burst was ignored must still stop the agent ---


def _busy_state(**kw: Any) -> adapter.CallState:
    state = _state(response_active=True, interrupted_mid_response=True, **kw)
    return state


@pytest.mark.parametrize(
    "text,expected",
    [
        ("आपको प्रस्तुत करते हैं", True),  # the real words from the call that ran on for 24 s
        ("nahi nahi IT business ke liye batao", True),
        ("पब्बे", False),  # one garbled word is not an interruption
        ("haan", False),
        ("ok theek hai", False),  # a nod
        ("", False),
    ],
)
def test_only_real_words_stop_the_agent(monkeypatch: pytest.MonkeyPatch, text: str, expected: bool) -> None:
    monkeypatch.setattr(settings, "voice_late_transcript_cut", True)
    assert adapter._late_transcript_is_real_interruption(_busy_state(), text) is expected


def test_late_transcript_cut_is_off_by_default_and_needs_an_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import Settings

    assert Settings.model_fields["voice_late_transcript_cut"].default is False
    assert adapter._late_transcript_is_real_interruption(_busy_state(), "nahi nahi IT business") is False
    monkeypatch.setattr(settings, "voice_late_transcript_cut", True)
    idle = _state(interrupted_mid_response=True)  # agent already quiet: nothing to cut
    assert adapter._late_transcript_is_real_interruption(idle, "nahi nahi IT business") is False
    not_overlapping = _state(response_active=True)  # agent talking but caller did not speak over it
    assert adapter._late_transcript_is_real_interruption(not_overlapping, "nahi nahi IT business") is False


def test_late_transcript_cut_never_cuts_the_greeting_guard_or_finish_first(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(settings, "voice_late_transcript_cut", True)
    guarded = _busy_state(greeting_guard_until=time.monotonic() + 5)
    assert adapter._late_transcript_is_real_interruption(guarded, "nahi nahi IT business") is False
    monkeypatch.setattr(settings, "voice_interruption_mode", "finish_first")
    assert adapter._late_transcript_is_real_interruption(_busy_state(), "nahi nahi IT business") is False


def test_late_transcript_is_a_known_cut_reason() -> None:
    from apps.api.channels.voice import turn_controller

    assert "late_transcript" in turn_controller.CUT_REASONS
