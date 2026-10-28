"""Phase-1 voice pipeline behaviour on the ElevenLabs path, driven through the
real adapter with the same Realtime events a live call produces and a fake
ElevenLabs session (no network, no database, no phone).

Covers: prewarming the next TTS socket, the non-blocking end-of-turn drain, and
the guarantees that make both safe — one session per turn, no audio from an
interrupted turn, no overlapping responses, nothing left open at call end.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

from apps.api.channels.voice import adapter, elevenlabs_client, latency
from apps.api.config import settings
from apps.api.core.conversation import voice_decision

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


class FakeSession:
    """Stands in for ElevenLabsTTSSession. Audio only ends when the test says
    so (``end()``), like a real turn whose tail is still being synthesized."""

    instances: list["FakeSession"] = []
    connect_delay = 0.0

    def __init__(self, voice_id: str | None = None, model_id: str | None = None) -> None:
        self.sent: list[str] = []
        self.finished = False
        self.closed = False
        self.connect_ms = 1
        self._audio: asyncio.Queue[str | None] = asyncio.Queue()
        FakeSession.instances.append(self)

    async def __aenter__(self) -> "FakeSession":
        if FakeSession.connect_delay:
            await asyncio.sleep(FakeSession.connect_delay)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def finish(self) -> None:
        self.finished = True

    async def audio_chunks(self) -> Any:
        while True:
            chunk = await self._audio.get()
            if chunk is None:
                return
            yield chunk

    def push(self, chunk: str) -> None:
        self._audio.put_nowait(chunk)

    def end(self) -> None:
        self._audio.put_nowait(None)


class _Phone:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Agent:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Log:
    def info(self, *a: Any, **k: Any) -> None:
        return None

    warning = info
    error = info


class Call:
    def __init__(self) -> None:
        self.state = adapter.CallState(
            user_id=USER_ID,
            conversation_id=uuid.uuid4(),
            org_id=ORG_ID,
            base_instructions="BASE",
            tts_provider="elevenlabs",
        )
        self.phone = _Phone()
        self.agent = _Agent()

    async def event(self, etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event(
            {"type": etype, **fields}, self.phone, self.agent, self.state, _Log()  # type: ignore[arg-type]
        )

    def audio_sent(self) -> list[str]:
        return [m["media"]["payload"] for m in self.phone.sent if m.get("event") == "playAudio"]

    def replies(self) -> list[dict[str, Any]]:
        return [m for m in self.agent.sent if m["type"] == "response.create"]

    def cleared(self) -> bool:
        return any(m.get("event") == "clearAudio" for m in self.phone.sent)


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    FakeSession.instances = []
    FakeSession.connect_delay = 0.0
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", FakeSession)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(settings, "conversation_voice_live", False)
    voice_decision.reset_flag_cache()

    persisted: list[tuple[str, Any]] = []

    async def fake_persist(state: Any, text: str, *, pending: Any = adapter._UNSET) -> None:
        persisted.append((text, state.pending_user_transcript if pending is adapter._UNSET else pending))

    monkeypatch.setattr(adapter, "_persist_voice_turn", fake_persist)

    async def no_plan(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "compute_voice_plan", no_plan)
    return persisted


def _flags(monkeypatch: pytest.MonkeyPatch, *, prewarm: bool, nonblocking: bool) -> None:
    monkeypatch.setattr(settings, "voice_elevenlabs_prewarm", prewarm)
    monkeypatch.setattr(settings, "voice_nonblocking_tts_finish", nonblocking)


async def _speak_text_of_a_turn(call: Call, text: str = "Namaste") -> FakeSession:
    await call.event("response.created")
    await call.event("response.text.delta", delta=text)
    session = call.state.eleven_session
    assert session is not None
    return session


# --- flags off: exactly the previous behaviour ----------------------------------------------------------


async def test_defaults_keep_the_original_lazy_open_and_inline_finish(
    monkeypatch: pytest.MonkeyPatch, fakes: list[tuple[str, Any]]
) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=False)
    call = Call()
    await call.event("response.created")
    assert FakeSession.instances == []  # nothing opened until text actually arrives
    session = await _speak_text_of_a_turn(call)
    assert len(FakeSession.instances) == 1

    session.push("QUJD")
    session.end()
    await asyncio.wait_for(call.event("response.text.done", text="Namaste"), timeout=1)
    # Inline: by the time the handler returns the tail was forwarded, the
    # session closed and the turn persisted — as before.
    assert call.audio_sent() == ["QUJD"]
    assert session.finished and session.closed
    assert fakes == [("Namaste", None)]
    assert call.state.eleven_finalize_task is None


async def test_inline_finish_still_blocks_the_event_loop_while_audio_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documents the original behaviour the new flag removes."""
    _flags(monkeypatch, prewarm=False, nonblocking=False)
    call = Call()
    await _speak_text_of_a_turn(call)  # audio never ends in this test
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(call.event("response.text.done", text="Namaste"), timeout=0.1)


# --- prewarm -----------------------------------------------------------------------------------------------


async def test_prewarm_opens_the_socket_at_response_created_and_reuses_it_for_the_first_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(FakeSession.instances) == 1  # opened before any text existed
    warmed = FakeSession.instances[0]

    await call.event("response.text.delta", delta="Namaste")
    assert len(FakeSession.instances) == 1  # claimed, not a second connection
    assert call.state.eleven_session is warmed
    assert warmed.sent == ["Namaste"]
    assert call.state.eleven_prewarm_task is None
    await adapter.cancel_pending_tasks(call.state)


async def test_a_slow_connect_is_waited_for_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    FakeSession.connect_delay = 0.05
    call = Call()
    await call.event("response.created")
    await call.event("response.text.delta", delta="Hi")  # arrives before the socket is ready
    assert len(FakeSession.instances) == 1
    assert FakeSession.instances[0].sent == ["Hi"]
    await adapter.cancel_pending_tasks(call.state)


async def test_a_stale_prewarmed_socket_is_replaced_not_spoken_into(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    warmed = FakeSession.instances[0]
    warmed.opened_at = time.monotonic() - (adapter._PREWARM_MAX_AGE_S + 5)  # idle past the provider's timeout
    await call.event("response.text.delta", delta="Namaste")
    assert warmed.closed and warmed.sent == []
    fresh = call.state.eleven_session
    assert fresh is not warmed and fresh.sent == ["Namaste"]
    await adapter.cancel_pending_tasks(call.state)


async def test_a_prewarmed_socket_the_provider_already_rejected_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    warmed = FakeSession.instances[0]
    warmed.failed = True
    await call.event("response.text.delta", delta="Namaste")
    assert warmed.closed and call.state.eleven_session is not warmed
    assert call.state.eleven_session.sent == ["Namaste"]
    await adapter.cancel_pending_tasks(call.state)


async def test_a_fresh_prewarmed_socket_is_still_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    warmed = FakeSession.instances[0]
    warmed.opened_at = time.monotonic()
    await call.event("response.text.delta", delta="Namaste")
    assert call.state.eleven_session is warmed and len(FakeSession.instances) == 1
    await adapter.cancel_pending_tasks(call.state)


async def test_prewarm_is_discarded_when_the_response_never_speaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool-call-only response: no text arrives, so the idle socket is closed
    instead of being left open against the provider."""
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    warmed = FakeSession.instances[0]
    await call.event("response.done")
    assert warmed.closed
    assert call.state.eleven_prewarm_task is None and call.state.eleven_session is None


async def test_prewarm_is_cancelled_by_an_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    FakeSession.connect_delay = 0.2
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)  # still connecting
    await adapter._teardown_elevenlabs_turn(call.state)
    assert call.state.eleven_prewarm_task is None
    await asyncio.sleep(0.3)
    # Never became a session anyone can speak through.
    assert call.state.eleven_session is None


async def test_prewarm_is_off_for_a_muted_response_and_for_native_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    muted = Call()
    muted.state.suppress_response_audio = True
    await muted.event("response.created")
    native = Call()
    native.state.tts_provider = "openai"
    await native.event("response.created")
    await asyncio.sleep(0.01)
    assert FakeSession.instances == []


async def test_a_failed_prewarm_falls_back_to_opening_at_the_first_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    attempts = 0
    real_enter = FakeSession.__aenter__

    async def flaky_enter(self: FakeSession) -> FakeSession:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("connect refused")
        return await real_enter(self)

    monkeypatch.setattr(FakeSession, "__aenter__", flaky_enter)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    await call.event("response.text.delta", delta="Hello")
    assert attempts == 2 and call.state.eleven_session is FakeSession.instances[1]
    assert call.state.eleven_session.sent == ["Hello"]
    await adapter.cancel_pending_tasks(call.state)


# --- non-blocking finish ---------------------------------------------------------------------------------------


async def test_nonblocking_finish_returns_at_once_and_keeps_the_agent_playing(
    monkeypatch: pytest.MonkeyPatch, fakes: list[tuple[str, Any]]
) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    session = await _speak_text_of_a_turn(call)

    await asyncio.wait_for(call.event("response.text.done", text="Namaste"), timeout=0.1)
    assert call.state.eleven_finalize_task is not None and not call.state.eleven_finalize_task.done()
    assert adapter._is_playing(call.state) and adapter._agent_busy(call.state)
    assert fakes == []  # not persisted until the audio has really been delivered

    session.push("QUJD")
    session.end()
    await asyncio.wait_for(call.state.eleven_finalize_task, timeout=1)
    assert call.audio_sent() == ["QUJD"]
    assert session.finished and session.closed
    assert fakes == [("Namaste", None)]
    # Advance beyond queued playback deterministically; Windows clock resolution
    # can exceed this tiny chunk and a 10ms sleep.
    played_at = call.state.playback_end + 1.0
    monkeypatch.setattr(adapter.time, "monotonic", lambda: played_at)
    assert not adapter._is_playing(call.state)


async def test_caller_speech_during_the_audio_tail_is_handled_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the change: with the old inline await, this event would sit
    unprocessed until the tail finished."""
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    await _speak_text_of_a_turn(call)
    await call.event("response.text.done", text="Namaste")

    await asyncio.wait_for(call.event("input_audio_buffer.speech_started"), timeout=0.1)
    assert call.state.caller_speaking and call.state.interrupted_mid_response  # seen as an overlap
    await adapter.cancel_pending_tasks(call.state)


async def test_cutting_the_agent_during_the_tail_stops_the_audio_and_the_persist(
    monkeypatch: pytest.MonkeyPatch, fakes: list[tuple[str, Any]]
) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    session = await _speak_text_of_a_turn(call)
    await call.event("response.text.done", text="Namaste")
    finalize = call.state.eleven_finalize_task
    assert finalize is not None

    await adapter._cut_now(call.agent, call.phone, call.state, _Log(), "test")  # type: ignore[arg-type]

    assert call.cleared()  # provider told to drop queued audio
    assert finalize.cancelled() or finalize.done()
    assert session.closed and call.state.eleven_session is None
    session.push("STALE")  # audio that arrives after the cut ...
    session.end()
    await asyncio.sleep(0.05)
    assert "STALE" not in call.audio_sent()  # ... is never played
    assert fakes == []  # the cut-off turn is not recorded as said
    assert call.state.suppress_response_audio is False or call.state.ack_stage in (None, "cancelling")


async def test_the_next_turn_never_shares_a_session_with_one_still_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    first = await _speak_text_of_a_turn(call, "Pehla jawab")
    await call.event("response.text.done", text="Pehla jawab")

    second_delta = asyncio.create_task(call.event("response.text.delta", delta="Doosra"))
    await asyncio.sleep(0.05)
    assert not second_delta.done()  # held until the first turn's audio is out
    assert len(FakeSession.instances) == 1

    first.push("T1")
    first.end()
    await asyncio.wait_for(second_delta, timeout=1)
    assert len(FakeSession.instances) == 2
    second = FakeSession.instances[1]
    assert second.sent == ["Doosra"] and "Doosra" not in first.sent
    assert call.audio_sent() == ["T1"]  # turn 1's audio went out first, in full
    await adapter.cancel_pending_tasks(call.state)


async def test_the_next_reply_waits_for_the_tail_to_finish_playing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No overlapping responses: response.done must not release the pending
    reply while the previous turn's audio is still being drained."""
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    session = await _speak_text_of_a_turn(call)
    await call.event("response.text.done", text="Namaste")
    call.state.reply_pending = True  # the caller already asked their next question

    await call.event("response.done")
    await asyncio.sleep(0.1)
    assert call.replies() == []  # held back

    session.end()
    await asyncio.sleep(0.15)
    assert len(call.replies()) == 1  # released exactly once, after the tail


async def test_persisted_pair_uses_the_transcript_from_when_the_turn_ended(
    monkeypatch: pytest.MonkeyPatch, fakes: list[tuple[str, Any]]
) -> None:
    """A transcript that arrives during the tail belongs to the NEXT turn and
    must neither be attached to this answer nor wiped."""
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    session = await _speak_text_of_a_turn(call)
    call.state.pending_user_transcript = "pehla sawaal"
    await call.event("response.text.done", text="Jawab")
    call.state.pending_user_transcript = "doosra sawaal"  # arrives while the tail drains

    session.end()
    await asyncio.wait_for(call.state.eleven_finalize_task, timeout=1)
    assert fakes == [("Jawab", "pehla sawaal")]
    assert call.state.pending_user_transcript == "doosra sawaal"


async def test_a_failure_while_finishing_is_contained_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, fakes: list[tuple[str, Any]]
) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=True)
    call = Call()
    session = await _speak_text_of_a_turn(call)

    async def boom() -> None:
        raise ConnectionError("socket dropped")

    monkeypatch.setattr(session, "finish", boom)
    await call.event("response.text.done", text="Namaste")
    await asyncio.wait_for(asyncio.wait({call.state.eleven_finalize_task}), timeout=1)
    assert session.closed and call.state.eleven_session is None
    assert call.state.eleven_finalize_task.exception() is None  # contained, not an orphaned error


# --- call end ---------------------------------------------------------------------------------------------------


async def test_cancel_pending_tasks_closes_everything_a_call_can_leave_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=True)
    call = Call()
    await call.event("response.created")
    await asyncio.sleep(0.01)
    warmed = FakeSession.instances[0]
    await call.event("response.text.delta", delta="Hi")
    await call.event("response.text.done", text="Hi")  # tail still draining
    await call.event("response.created")  # next turn's prewarm
    await asyncio.sleep(0.01)

    await adapter.cancel_pending_tasks(call.state)

    assert warmed.closed
    assert all(s.closed for s in FakeSession.instances)
    st = call.state
    assert st.eleven_session is None and st.eleven_prewarm_task is None and st.eleven_finalize_task is None
    assert st.eleven_forward_task is None


# --- latency wiring ---------------------------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    warning = info
    error = info

    def named(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.events if n == name]


async def test_a_prewarmed_turn_logs_every_stage_and_first_audio_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, prewarm=True, nonblocking=False)
    rec = _Recorder()
    monkeypatch.setattr(latency, "logger", rec)
    latency.STATS.reset()
    call = Call()
    call.state.latency = latency.LatencyTracker(org_id=ORG_ID, call_uuid="c-1", provider="plivo", enabled=True)

    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")  # schedules the reply
    await asyncio.sleep(0.05)  # reply fires: response.create goes out
    assert len(call.replies()) == 1

    await call.event("response.created")
    await asyncio.sleep(0.02)
    await call.event("response.text.delta", delta="Namaste")
    FakeSession.instances[0].push("QUJD")
    await asyncio.sleep(0.05)

    (line,) = rec.named("voice_turn_latency")
    assert line["org_id"] == str(ORG_ID) and line["call_uuid"] == "c-1"
    for stage in (
        "speech_stopped",
        "committed",
        "response_requested",
        "openai_response_created",
        "openai_first_output",
        "tts_connect_started",
        "tts_ready",
        "tts_first_chunk",
        "first_audio_sent",
    ):
        assert stage in line["marks_ms"], stage
    assert line["tts_prewarmed"] is True
    assert line["segments_ms"]["tts_connect_wait"] == 0  # hidden behind the model's first token
    await adapter.cancel_pending_tasks(call.state)


async def test_interruption_timings_are_recorded_around_a_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, prewarm=False, nonblocking=False)
    monkeypatch.setattr(adapter, "LIVE_FALLBACK_CUT_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "EARLY_CUT_SECONDS", 0.05)
    rec = _Recorder()
    monkeypatch.setattr(latency, "logger", rec)
    call = Call()
    call.state.latency = latency.LatencyTracker(enabled=True)
    call.state.response_active = True
    call.state.playback_end = time.monotonic() + 5

    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.2)  # held long enough to be cut
    assert call.cleared()
    await call.event("response.created")  # the next response starts

    (line,) = rec.named("voice_interruption_latency")
    assert line["detected_to_cleared_ms"] >= 0 and line["detected_to_next_response_ms"] >= 0
