"""Barge-in latency and stale-response regression tests.

Driven through the real voice adapter with mocked provider events, a fake ElevenLabs session and a fake phone
socket that timestamps everything it is sent. Nothing touches the network, a database or a phone, so the
latencies asserted here are the latencies OUR code adds (decision + clear ordering + teardown); they say nothing
about provider/network time.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from typing import Any

import pytest

from apps.api.channels.voice import adapter, elevenlabs_client
from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.tests.test_voice_interruption import Call, _flags, base  # noqa: F401  (base is an autouse fixture)
from apps.api.tests.test_voice_tts_turns import FakeSession
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS

SLOW_CLOSE_SECONDS = 1.5


class TimedPhone:
    """The provider socket: records every message with the moment it was sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.at: list[float] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))
        self.at.append(time.monotonic())

    def first(self, event: str) -> float | None:
        for msg, at in zip(self.sent, self.at):
            if msg.get("event") == event:
                return at
        return None


class SlowCloseSession(FakeSession):
    """An ElevenLabs session whose close handshake is slow (a provider that takes its time to acknowledge)."""

    async def __aexit__(self, *exc: Any) -> None:
        await asyncio.sleep(SLOW_CLOSE_SECONDS)
        self.closed = True


def _timed_call(**state: Any) -> Call:
    call = Call(tts_provider="elevenlabs", **state)
    call.phone = TimedPhone()  # type: ignore[assignment]
    return call


async def _agent_speaking(call: Call, rid: str = "resp_1") -> FakeSession:
    """An ElevenLabs answer that is streaming and already audible at the provider."""
    await call.event("response.created", response={"id": rid})
    await call.event("response.text.delta", delta="Dava India franchise mein investment", response_id=rid)
    session = call.state.eleven_session
    assert isinstance(session, FakeSession)
    session.push("QUFB")
    await asyncio.sleep(0.05)
    assert call.audio() == ["QUFB"]
    call.state.playback_end = time.monotonic() + 6.0
    return session


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_native_barge_in=True)


# 1 ------------------------------------------------------------------------------------------------------------


async def test_playback_cancellation_begins_promptly_even_when_the_tts_socket_is_slow_to_close(
    monkeypatch: pytest.MonkeyPatch, native: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Root cause of the multi-second stop: clearAudio used to be sent only AFTER the ElevenLabs socket had
    closed. With a close that takes SLOW_CLOSE_SECONDS the caller kept hearing the agent for that long."""
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", SlowCloseSession)
    call = _timed_call()
    await _agent_speaking(call)
    started = time.monotonic()
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.3)

    cleared_at = call.phone.first("clearAudio")
    assert cleared_at is not None
    latency = cleared_at - started
    with capsys.disabled():
        print(f"\n[measured, mocked] speech_started -> clearAudio: {latency * 1000:.0f} ms "
              f"(debounce {adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS * 1000:.0f} ms, "
              f"simulated TTS close {SLOW_CLOSE_SECONDS * 1000:.0f} ms)")
    assert latency < adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.25
    assert latency < SLOW_CLOSE_SECONDS  # i.e. not queued behind the socket close
    await adapter.cancel_pending_tasks(call.state)


async def test_the_real_elevenlabs_session_does_not_wait_out_a_slow_close(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowWS(_FakeElevenLabsWS):
        async def close(self) -> None:
            await asyncio.sleep(30)

    ws = SlowWS(server_messages=[])

    async def fake_connect(url: str) -> SlowWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client, "CLOSE_TIMEOUT_SECONDS", 0.1)
    session = await elevenlabs_client.ElevenLabsTTSSession().__aenter__()
    t0 = time.monotonic()
    await session.__aexit__(None, None, None)
    assert time.monotonic() - t0 < 1.0


# 2 ------------------------------------------------------------------------------------------------------------


async def test_a_genuine_interruption_cancels_queued_and_in_flight_audio(native: None) -> None:
    call = _timed_call()
    session = await _agent_speaking(call)
    session.push("Q1FD")  # produced but not yet forwarded when the caller starts talking
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)

    assert call.cleared()  # audio queued at the provider is dropped
    assert call.state.playback_end == 0.0
    assert session.closed and call.state.eleven_session is None
    assert call.state.eleven_forward_task is None
    before = list(call.audio())
    session.push("INFLIGHT")  # the provider was still generating
    session.end()
    await asyncio.sleep(0.1)
    assert call.audio() == before and "INFLIGHT" not in call.audio()
    await adapter.cancel_pending_tasks(call.state)


# 3 ------------------------------------------------------------------------------------------------------------


async def test_late_chunks_and_events_of_a_cancelled_response_are_discarded_even_after_the_mute_is_lifted(
    native: None,
) -> None:
    dispatched: list[str] = []

    async def fake_dispatch(name: str, *a: Any, **k: Any) -> dict[str, Any]:
        dispatched.append(name)
        return {"status": "ok"}

    adapter._dispatch_realtime_tool = fake_dispatch  # type: ignore[assignment]
    call = _timed_call()
    session = await _agent_speaking(call, "resp_1")
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)
    assert "resp_1" in call.state.superseded_response_ids and call.state.cancelled_response_ids == set()

    call.state.suppress_response_audio = False  # the blunt mute is gone: only ids protect now
    before = len(call.phone.sent)
    session.push("STALE_TTS")
    await call.event("response.output_audio.delta", delta="STALE_OAI", response_id="resp_1")
    await call.event("response.text.delta", delta="aur bhi", response_id="resp_1")
    await call.event("response.function_call_arguments.done", name="book_appointment", call_id="c9", response_id="resp_1")
    await asyncio.sleep(0.05)
    assert len(call.phone.sent) == before and dispatched == []
    assert len(FakeSession.instances) == 1  # the late text did not open a new TTS session
    await adapter.cancel_pending_tasks(call.state)


# 4 + 8 --------------------------------------------------------------------------------------------------------


async def test_a_new_question_while_the_previous_answer_is_generating_gets_exactly_one_fresh_reply(
    native: None,
) -> None:
    call = _timed_call()
    call.state.pending_user_transcript = "franchise ke baare mein batao"
    call.state.pending_utt = 1
    call.state.utt_seq = 1
    await _agent_speaking(call, "resp_1")

    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="item_2")
    assert call.replies() == []  # resp_1 is still generating (muted): nothing may talk over it
    assert call.state.utt_seq == 2

    # resp_1 keeps producing text for a while, all of it muted.
    await call.event("response.text.delta", delta="... baaki details", response_id="resp_1")
    await call.event("response.done", response={"id": "resp_1", "status": "completed"})
    await asyncio.sleep(0.1)

    assert len(call.replies()) == 1  # the new question is answered once
    # The reply was fired before the new question's transcript arrived: it must not be filed under Q1.
    assert call.state.answering_transcript is None and call.state.answering_utt == 2

    await call.event("response.created", response={"id": "resp_2"})
    await call.event("conversation.item.input_audio_transcription.completed", item_id="item_2", transcript="aur fees kitni hai")
    assert call.state.pending_user_transcript == "aur fees kitni hai"
    await call.event("response.text.delta", delta="Fees do lakh hai", response_id="resp_2")
    await asyncio.sleep(0.05)
    assert call.state.eleven_session is not None
    assert call.state.eleven_session.sent[-1] == "Fees do lakh hai"
    assert "baaki details" not in "".join(s.sent[0] for s in FakeSession.instances if s.sent and s is call.state.eleven_session)
    await adapter.cancel_pending_tasks(call.state)


async def test_the_latest_question_is_never_filed_or_decided_as_the_previous_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = adapter.CallState(user_id=None, conversation_id=None, org_id=None, base_instructions="B")  # type: ignore[arg-type]
    state.pending_user_transcript = "franchise ke baare mein batao"
    state.pending_utt, state.utt_seq = 1, 2  # a second utterance is committed; its transcript is not here yet
    assert adapter._current_transcript(state) is None

    state.pending_utt = 2
    assert adapter._current_transcript(state) == "franchise ke baare mein batao"

    # Decision layer: a plan computed for Q1 must not steer the reply to Q2.
    applied: list[str] = []

    async def fake_apply(*a: Any, **k: Any) -> None:
        applied.append(k.get("text", ""))

    async def plan() -> Any:
        return types.SimpleNamespace(config=None, understanding=None, memory={})

    monkeypatch.setattr(voice_decision, "cached_voice_mode", lambda org: ("live", True))
    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    state.voice_decision_text = "franchise ke baare mein batao"
    state.voice_decision_started = time.monotonic()
    state.pending_utt = 1  # Q2 committed, transcript pending
    state.voice_decision_task = asyncio.ensure_future(plan())
    assert await adapter._voice_decision_response(state) is None
    assert applied == []


# 5 ------------------------------------------------------------------------------------------------------------


async def test_an_older_response_completing_after_a_newer_turn_began_changes_nothing(native: None) -> None:
    call = _timed_call()
    await _agent_speaking(call, "resp_1")
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="item_2")
    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await asyncio.sleep(0.1)
    await call.event("response.created", response={"id": "resp_2"})
    await call.event("response.text.delta", delta="Naya jawab", response_id="resp_2")
    session = call.state.eleven_session
    assert session is not None and call.state.response_id == "resp_2"

    # Everything resp_1 still had in flight lands now, out of order.
    await call.event("response.text.delta", delta="PURANA", response_id="resp_1")
    await call.event("response.text.done", text="PURANA", response_id="resp_1")
    await call.event("response.done", response={"id": "resp_1", "status": "completed"})
    assert call.state.response_active and call.state.response_id == "resp_2"
    assert call.state.eleven_session is session and session.sent == ["Naya jawab"]
    await adapter.cancel_pending_tasks(call.state)


async def test_a_response_cut_before_it_was_created_is_born_superseded(native: None) -> None:
    call = _timed_call()
    call.state.completed_response_ids.add("resp_0")
    call.state.response_id = "resp_0"
    call.state.response_active = True  # reply requested, response.created not here yet
    call.state.playback_end = time.monotonic() + 3
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)
    await call.event("response.created", response={"id": "resp_1"})
    assert "resp_1" in call.state.superseded_response_ids
    await call.event("response.text.delta", delta="should never be spoken", response_id="resp_1")
    assert call.state.eleven_session is None and FakeSession.instances == []
    await adapter.cancel_pending_tasks(call.state)


async def test_a_tts_retry_cannot_bring_back_a_cancelled_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_tts_recovery=True)
    call = _timed_call()
    call.state.eleven_text_buffer = "adhoora jawab"
    call.state.suppress_response_audio = True  # the caller already cut this turn
    await adapter._tts_failed(call.state, call.log, "stream_ended_early")
    assert FakeSession.instances == [] and call.state.tts_failures == 0


# 6 ------------------------------------------------------------------------------------------------------------


async def test_partial_transcripts_update_without_creating_duplicate_turns() -> None:
    call = Call()
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_started")  # the VAD re-fires inside one continuous utterance
    for part in ("mujhe", " price", " batao"):
        await adapter.handle_live_delta(part, call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    assert call.state.utterance_text == "mujhe price batao"
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed", item_id="item_1")
    for _ in range(2):  # the final transcript is delivered twice
        await call.event(
            "conversation.item.input_audio_transcription.completed", item_id="item_1", transcript="mujhe price batao"
        )
    await asyncio.sleep(0.1)

    assert len(call.replies()) == 1
    assert call.state.utt_seq == 1 and call.state.transcripts_outstanding == 0
    assert call.state.overlap_transcripts == []
    await adapter.cancel_pending_tasks(call.state)


# 7 ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("spoken", ["haan ji", "theek hai", "hmm"])
async def test_short_acknowledgements_do_not_interrupt(native: None, spoken: str) -> None:
    call = _timed_call()
    await _agent_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    await adapter.handle_live_delta(spoken, call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.1)
    await call.event("input_audio_buffer.speech_stopped")  # they stop; it was only a nod
    await asyncio.sleep(0.4)
    assert not call.cleared() and call.state.eleven_session is not None
    assert call.state.superseded_response_ids == set()
    await adapter.cancel_pending_tasks(call.state)


async def test_a_short_noise_burst_does_not_interrupt(native: None) -> None:
    call = _timed_call()
    await _agent_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.1)
    await call.event("input_audio_buffer.speech_stopped")  # a cough / click
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.2)
    assert not call.cleared() and call.state.eleven_session is not None
    await adapter.cancel_pending_tasks(call.state)


async def test_energy_gate_blocks_a_soft_sound_but_lets_sustained_speech_through(
    monkeypatch: pytest.MonkeyPatch, native: None
) -> None:
    class Gate:
        run_seconds = 0.5
        run_mean_prob = 0.9
        confident = False

        def sustained(self, seconds: float) -> bool:
            return self.confident

    gate = Gate()
    call = _timed_call(vad=gate)
    await _agent_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(adapter.NATIVE_BARGE_IN_MIN_SPEECH_SECONDS + 0.3)
    assert not call.cleared()  # soft/patchy: held back
    gate.confident = True
    await asyncio.sleep(0.2)
    assert call.cleared()
    await adapter.cancel_pending_tasks(call.state)


async def test_local_vad_ends_the_wait_for_a_late_transcript_in_the_live_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default (non-native) path: with no transcript yet, the cut used to wait up to LIVE_NO_TEXT_CUT_SECONDS."""
    monkeypatch.setattr(adapter, "LIVE_FALLBACK_CUT_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "LIVE_NO_TEXT_CUT_SECONDS", 2.0)

    class Gate:
        def sustained(self, seconds: float) -> bool:
            return True

    call = _timed_call(vad=Gate(), live=types.SimpleNamespace(healthy=True))
    await _agent_speaking(call)
    started = time.monotonic()
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(0.4)
    cleared_at = call.phone.first("clearAudio")
    assert cleared_at is not None and cleared_at - started < 0.4
    await adapter.cancel_pending_tasks(call.state)
