"""Turn stamping: a transcript that arrives after the conversation has moved past it is dropped, never applied to the
current question (Question A answered after the assistant has shifted to Question B)."""

from __future__ import annotations

import asyncio

import pytest

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.tests.test_voice_interruption import Call, base  # noqa: F401 - base is an autouse fixture


_real_persist = adapter._persist_voice_turn  # captured before the shared fixture swaps in a stub


@pytest.fixture(autouse=True)
def _no_db(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    saved: list[tuple[str, str]] = []

    async def fake_persist_turn(db, **kw):  # noqa: ANN001
        saved.append((kw["user_text"], kw["assistant_text"]))

    class _Ctx:
        async def __aenter__(self):  # noqa: ANN204
            return object()

        async def __aexit__(self, *a):  # noqa: ANN002, ANN204
            return False

    monkeypatch.setattr(adapter, "persist_turn", fake_persist_turn)
    monkeypatch.setattr(adapter, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(adapter, "schedule_voice_shadow", lambda *a, **k: None)
    return saved


async def _say(call: Call, item: str) -> None:
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id=item)


async def _transcript(call: Call, item: str, text: str) -> None:
    await call.event("conversation.item.input_audio_transcription.completed", item_id=item, transcript=text)


async def test_in_order_transcripts_are_applied() -> None:
    call = Call()
    await _say(call, "i1")
    await _transcript(call, "i1", "pricing kya hai")
    assert call.state.pending_user_transcript == "pricing kya hai"
    assert call.state.pending_utt == 1


async def test_a_transcript_for_a_recorded_turn_is_dropped(_no_db: list[tuple[str, str]]) -> None:
    call = Call()
    await _say(call, "i1")  # answer to question A is given without its transcript ever arriving
    call.state.answering_utt = 1
    await _real_persist(call.state, "answer to A")
    assert call.state.consumed_utt == 1
    await _say(call, "i2")  # the assistant has moved on to question B
    await _transcript(call, "i1", "A ka jawab: haan")  # ...and the lagging transcript for A arrives now
    assert call.state.pending_user_transcript is None
    assert call.state.transcripts_outstanding == 1  # only i2 is still outstanding
    await _transcript(call, "i2", "B ka jawab: nahi")
    assert call.state.pending_user_transcript == "B ka jawab: nahi"


async def test_an_older_transcript_cannot_overwrite_a_newer_one() -> None:
    call = Call()
    await _say(call, "i1")
    await _say(call, "i2")
    await _transcript(call, "i2", "second")
    await _transcript(call, "i1", "first")
    assert call.state.pending_user_transcript == "second"


async def test_stale_pending_never_pairs_with_the_next_answer(_no_db: list[tuple[str, str]]) -> None:
    call = Call()
    await _say(call, "i1")
    call.state.answering_utt = 1
    await _real_persist(call.state, "answer A")
    await _say(call, "i2")
    await _transcript(call, "i1", "late A")
    call.state.answering_utt = 2
    await _real_persist(call.state, "answer B")
    assert _no_db[-1] == (adapter.NO_TRANSCRIPT_PLACEHOLDER, "answer B")


async def test_transcripts_without_ids_match_in_commit_order() -> None:
    call = Call()
    await call.event("input_audio_buffer.committed")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript="one")
    assert call.state.pending_utt == 1


async def test_turn_id_is_stamped_when_speech_starts() -> None:
    call = Call()
    await call.event("response.created", response={"id": "r1"})
    await call.event("input_audio_buffer.speech_started")
    stamped = call.state.speech_turn_id
    await call.event("response.created", response={"id": "r2"})
    await call.event("input_audio_buffer.committed", item_id="i1")
    assert stamped == 1 and call.state.assistant_turn_id == 2
    assert call.state.utt_queue[-1][2] == 1


async def test_concurrent_persists_are_serialized(_no_db: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    async def slow_persist(db, **kw):  # noqa: ANN001
        order.append("start " + kw["assistant_text"])
        await asyncio.sleep(0.02)
        order.append("end " + kw["assistant_text"])

    monkeypatch.setattr(adapter, "persist_turn", slow_persist)
    call = Call()
    await asyncio.gather(
        _real_persist(call.state, "a", pending=None),
        _real_persist(call.state, "b", pending=None),
    )
    assert order == ["start a", "end a", "start b", "end b"]
