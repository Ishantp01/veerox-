"""Phase 3: interruption cancellation and natural turn-taking, driven through
the real voice adapter with the Realtime events a live call produces.

Nothing here touches the network, a database or a phone. The OpenAI side is a
fake socket, so these tests prove OUR behaviour (what we send, drop, wait for
and fall back to); they cannot prove how the real Realtime API reacts to
``response.cancel``. That needs a live call (see the Phase 3 report).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
import types
from typing import Any

import pytest
import websockets

from apps.api.channels.voice import adapter, elevenlabs_client, turn_controller
from apps.api.channels.voice.voice_profile import ResolvedVoice
from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS
from apps.api.tests.test_voice_tts_turns import FakeSession

# Captured at import, before any fixture swaps in the fake.
_RealElevenLabsSession = elevenlabs_client.ElevenLabsTTSSession

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


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


class _BrokenAgent(_Agent):
    """The OpenAI socket dies the moment we try to send on it."""

    async def send(self, message: str) -> None:
        raise websockets.exceptions.ConnectionClosedError(None, None)


class _Log:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, *a: Any, **k: Any) -> None:
        return None

    def warning(self, event: str, *a: Any, **k: Any) -> None:
        self.warnings.append(event)


class Call:
    def __init__(self, org: uuid.UUID = ORG_A, agent: _Agent | None = None, **state: Any) -> None:
        self.state = adapter.CallState(
            user_id=USER,
            conversation_id=uuid.uuid4(),
            org_id=org,
            base_instructions="BASE INSTRUCTIONS",
        )
        for key, value in state.items():
            setattr(self.state, key, value)
        self.phone = _Phone()
        self.agent = agent or _Agent()
        self.log = _Log()

    async def event(self, etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event(
            {"type": etype, **fields}, self.phone, self.agent, self.state, self.log  # type: ignore[arg-type]
        )

    def cancels(self) -> list[dict[str, Any]]:
        return [m for m in self.agent.sent if m["type"] == "response.cancel"]

    def replies(self) -> list[dict[str, Any]]:
        return [m for m in self.agent.sent if m["type"] == "response.create"]

    def cleared(self) -> bool:
        return any(m.get("event") == "clearAudio" for m in self.phone.sent)

    def audio(self) -> list[str]:
        return [m["media"]["payload"] for m in self.phone.sent if m.get("event") == "playAudio"]

    async def agent_starts_answering(self, rid: str = "resp_1", *, question: str = "franchise ke baare mein batao") -> None:
        """A normal reply is in flight and audibly playing."""
        self.state.pending_user_transcript = question
        self.state.answering_transcript = question
        await self.event("response.created", response={"id": rid})
        self.state.resp_text = "Dava India franchise mein investment"
        self.state.playback_end = time.monotonic() + 5.0


@pytest.fixture(autouse=True)
def base(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """Defaults every test starts from: all Phase 3 flags OFF, no decision layer,
    short interruption timers, persistence recorded instead of written."""
    for name, value in (
        ("voice_safe_cancel", False),
        ("voice_smart_turn_taking", False),
        ("voice_endpoint_hold", False),
        ("voice_elevenlabs_prewarm", False),
        ("voice_nonblocking_tts_finish", False),
        ("conversation_voice_live", False),
        ("voice_cancel_timeout_seconds", 2.0),
    ):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(adapter, "EARLY_CUT_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "LIVE_FALLBACK_CUT_SECONDS", 0.05)
    voice_decision.reset_flag_cache()

    async def no_plan(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "compute_voice_plan", no_plan)

    persisted: list[tuple[str, Any]] = []

    async def fake_persist(state: Any, text: str, *, pending: Any = adapter._UNSET) -> None:
        persisted.append((text, state.pending_user_transcript if pending is adapter._UNSET else pending))
        state.answering_transcript = None

    monkeypatch.setattr(adapter, "_persist_voice_turn", fake_persist)
    FakeSession.instances = []
    FakeSession.connect_delay = 0.0
    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", FakeSession)
    return persisted


def _flags(monkeypatch: pytest.MonkeyPatch, **flags: Any) -> None:
    for name, value in flags.items():
        monkeypatch.setattr(settings, name, value)


async def _interrupt(call: Call, wait: float = 0.2) -> None:
    """The caller talks over the agent long enough for the cut timer to fire."""
    await call.event("input_audio_buffer.speech_started")
    await asyncio.sleep(wait)


async def _caller_finishes_turn(call: Call, text: str) -> None:
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript=text)


# --- default behaviour is unchanged -----------------------------------------------------------------------


async def test_without_the_flag_an_interruption_mutes_and_never_cancels() -> None:
    call = Call()
    await call.agent_starts_answering()
    await _interrupt(call)

    assert call.cleared() and call.state.suppress_response_audio
    assert call.state.ack_stage == "cancelling"
    assert call.cancels() == []  # exactly the previous behaviour
    assert call.state.cancelled_response_ids == set() and call.state.cancel_task is None


async def test_without_the_flag_a_late_delta_is_still_muted_by_the_old_flag_not_by_id() -> None:
    call = Call()
    await call.agent_starts_answering()
    await _interrupt(call)
    before = len(call.phone.sent)
    await call.event("response.output_audio.delta", delta="AAAA", response_id="resp_1")
    assert len(call.phone.sent) == before  # muted via suppress_response_audio, as before


# --- safe cancellation ----------------------------------------------------------------------------------------


async def test_interruption_cancels_the_active_response_and_clears_playback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)

    assert call.cleared()  # the caller hears silence at once
    assert call.cancels() == [{"type": "response.cancel", "response_id": "resp_1"}]
    assert "resp_1" in call.state.cancelled_response_ids
    assert call.state.cancel_task is not None and call.state.cancel_sent_at > 0
    await adapter.cancel_pending_tasks(call.state)


async def test_the_cancelled_response_confirming_releases_the_answer_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await _caller_finishes_turn(call, "ismein profit kitna hoga")
    assert call.replies() == []  # nothing speaks over a response that has not ended

    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await asyncio.sleep(0.1)

    assert len(call.replies()) == 1  # the interruption is answered, exactly once
    assert call.state.cancel_sent_at == 0.0 and call.state.cancel_task is None
    assert call.state.ack_stage is None and not call.state.suppress_response_audio


async def test_late_events_of_a_cancelled_response_never_play_or_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    dispatched: list[str] = []

    async def fake_dispatch(name: str, *a: Any, **k: Any) -> dict[str, Any]:
        dispatched.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake_dispatch)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    # The cancelled response is replaced by the answer to the interruption.
    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    call.state.suppress_response_audio = False  # the old mute is over: only ids can protect now

    before = len(call.phone.sent)
    await call.event("response.output_audio.delta", delta="STALE", response_id="resp_1")
    await call.event("response.function_call_arguments.done", name="book_appointment", call_id="c1", response_id="resp_1")
    assert len(call.phone.sent) == before and dispatched == []

    # A different, live response is not affected.
    await call.event("response.output_audio.delta", delta="FRESH", response_id="resp_2")
    assert "FRESH" in call.audio()
    await call.event("response.function_call_arguments.done", name="capture_lead", call_id="c2", response_id="resp_2")
    assert dispatched == ["capture_lead"]
    await adapter.cancel_pending_tasks(call.state)


async def test_the_same_response_is_never_cancelled_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    for _ in range(3):  # rapid repeated cut attempts on one response
        await adapter._cut_now(call.agent, call.phone, call.state, call.log, "test")  # type: ignore[arg-type]
    assert len(call.cancels()) == 1
    await adapter.cancel_pending_tasks(call.state)


async def test_rapid_interruptions_cancel_each_response_once_and_never_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await _caller_finishes_turn(call, "kitna paisa lagega")
    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1

    # The answer starts, and the caller cuts in again straight away.
    await call.agent_starts_answering("resp_2", question="kitna paisa lagega")
    await _interrupt(call)
    await _caller_finishes_turn(call, "nahi nahi pehle profit batao")
    assert [c["response_id"] for c in call.cancels()] == ["resp_1", "resp_2"]
    # The second answer waits for the second response to end: still exactly one reply so far.
    assert len(call.replies()) == 1

    await call.event("response.done", response={"id": "resp_2", "status": "cancelled"})
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 2
    await adapter.cancel_pending_tasks(call.state)


# --- fallback when cancellation misbehaves --------------------------------------------------------------------


async def test_an_error_after_the_cancel_trips_the_breaker_and_later_cuts_only_mute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    assert len(call.cancels()) == 1

    await call.event("error", error={"code": "invalid_request_error", "message": "bad cancel"})
    assert call.state.cancel_disabled and "voice_cancel_disabled" in call.log.warnings
    await call.event("response.done", response={"id": "resp_1"})

    await call.agent_starts_answering("resp_2")
    await _interrupt(call)
    assert call.cleared() and call.state.suppress_response_audio  # still cut off for the caller
    assert len(call.cancels()) == 1  # but no second response.cancel on this call
    await adapter.cancel_pending_tasks(call.state)


async def test_cancel_not_active_is_benign_and_does_not_disable_cancelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await _caller_finishes_turn(call, "kya hai")
    # The answer had already finished by the time the cancel landed.
    await call.event("error", error={"code": "response_cancel_not_active"})
    await asyncio.sleep(0.1)

    assert not call.state.cancel_disabled
    assert call.state.cancel_task is None and call.state.cancel_sent_at == 0.0
    assert len(call.replies()) == 1 and call.state.ack_stage is None


async def test_a_cancel_that_is_never_confirmed_trips_the_breaker_then_is_written_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_cancel_timeout_seconds=0.05)
    monkeypatch.setattr(adapter, "CANCEL_HARD_TIMEOUT_SECONDS", 0.7)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await _caller_finishes_turn(call, "profit kitna hoga")

    await asyncio.sleep(0.12)  # soft timeout: don't trust cancel again, keep waiting for the natural end
    assert call.state.cancel_disabled
    assert call.state.ack_stage == "cancelling" and call.replies() == []

    await asyncio.sleep(0.6)  # hard timeout: never deadlock waiting for a response.done that is not coming
    assert call.state.ack_stage is None and call.state.cancel_sent_at == 0.0
    assert len(call.replies()) == 1  # the written-off response no longer blocks the answer


async def test_a_session_close_right_after_a_cancel_disables_cancelling_for_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    adapter.note_openai_closed(call.state, call.log)
    assert call.state.cancel_disabled and "voice_cancel_suspected_session_close" in call.log.warnings

    # A close long after a cancel is unrelated.
    other = Call()
    other.state.cancel_sent_at = time.monotonic() - 60
    adapter.note_openai_closed(other.state, other.log)
    assert not other.state.cancel_disabled
    await adapter.cancel_pending_tasks(call.state)


async def test_websocket_dying_during_the_interruption_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call(agent=_BrokenAgent())
    await call.agent_starts_answering("resp_1")
    await adapter._cut_now(call.agent, call.phone, call.state, call.log, "test")  # type: ignore[arg-type]

    assert call.cleared()  # the caller is already protected
    assert call.state.cancel_disabled and call.state.suppress_response_audio  # falls back to muting
    assert call.state.cancel_task is None  # no watchdog for a cancel that never left


async def test_reconnect_forgets_every_old_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_smart_turn_taking=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    task = call.state.cancel_task
    generation = call.state.generation

    adapter.reset_after_reconnect(call.state)
    await asyncio.sleep(0)

    st = call.state
    assert st.response_id is None and st.cancelled_response_ids == set()
    assert st.cancel_sent_at == 0.0 and st.cancel_task is None and not st.priority_reply
    assert st.generation > generation and (task is None or task.cancelled() or task.done())


# --- interruption during ElevenLabs streaming / normalized speech --------------------------------------------------


async def test_interruption_during_elevenlabs_streaming_stops_audio_and_ignores_late_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call(tts_provider="elevenlabs")
    await call.event("response.created", response={"id": "resp_1"})
    await call.event("response.text.delta", delta="Dava India franchise", response_id="resp_1")
    session = call.state.eleven_session
    assert isinstance(session, FakeSession)
    session.push("Q0hVTks=")
    await asyncio.sleep(0.05)
    assert call.audio() == ["Q0hVTks="]
    call.state.playback_end = time.monotonic() + 5

    await _interrupt(call)
    assert session.closed and call.state.eleven_session is None
    assert call.cancels() == [{"type": "response.cancel", "response_id": "resp_1"}]

    session.push("STALE")  # audio the provider produced after the cut
    session.end()
    await call.event("response.text.delta", delta="aur bhi text", response_id="resp_1")
    await asyncio.sleep(0.05)
    assert "STALE" not in call.audio()
    assert len(FakeSession.instances) == 1  # the late text did not open a new session
    await adapter.cancel_pending_tasks(call.state)


async def test_the_audio_forwarder_stops_when_the_generation_changes() -> None:
    call = Call(tts_provider="elevenlabs")
    await call.event("response.created", response={"id": "resp_1"})
    await call.event("response.text.delta", delta="Namaste", response_id="resp_1")
    session = call.state.eleven_session
    assert session is not None
    session.push("AAAA")
    await asyncio.sleep(0.05)
    call.state.generation += 1  # a cut happened
    session.push("BBBB")
    await asyncio.sleep(0.05)
    assert call.audio() == ["AAAA"]
    await adapter.cancel_pending_tasks(call.state)


async def test_interruption_during_normalized_speech_does_not_flush_buffered_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = _FakeElevenLabsWS(server_messages=[])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    profile = ResolvedVoice(
        voice_id="v" * 12,
        model_id="eleven_flash_v2_5",
        voice_settings={"stability": 0.42, "similarity_boost": 0.8},
        normalize=True,
    )
    session = await _RealElevenLabsSession(profile=profile).__aenter__()
    await session.send_text("Aapka bill")
    await session.send_text(" Rs. 15")  # an amount still being spoken: held back by the normalizer
    sent_before_cut = len(ws.sent)

    await session.__aexit__(None, None, None)  # the caller interrupts

    assert len(ws.sent) == sent_before_cut  # the half-said amount is never flushed to TTS
    assert ws.closed
    assert all(m.get("text") != "" for m in ws.sent[1:])  # and no end-of-turn flush was sent


# --- call termination -------------------------------------------------------------------------------------------------


async def test_call_end_leaves_no_task_or_socket_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_elevenlabs_prewarm=True, voice_nonblocking_tts_finish=True)
    call = Call(tts_provider="elevenlabs")
    await call.event("response.created", response={"id": "resp_1"})
    await asyncio.sleep(0.02)
    await call.event("response.text.delta", delta="Hi", response_id="resp_1")
    call.state.playback_end = time.monotonic() + 5
    await _interrupt(call)  # cancel watchdog running
    await call.event("response.text.done", text="Hi", response_id="resp_2")  # tail of a new turn
    await call.event("response.created", response={"id": "resp_2"})  # prewarm for the next turn
    await asyncio.sleep(0.02)
    watchdog = call.state.cancel_task
    assert watchdog is not None

    await adapter.cancel_pending_tasks(call.state)

    assert watchdog.done()
    assert all(s.closed for s in FakeSession.instances)
    st = call.state
    assert st.eleven_session is None and st.eleven_prewarm_task is None and st.eleven_finalize_task is None


async def test_one_calls_cancellations_never_affect_another_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    a, b = Call(ORG_A), Call(ORG_B)
    await a.agent_starts_answering("resp_1")
    await b.agent_starts_answering("resp_1")  # the same response id on another call
    await _interrupt(a)

    assert a.state.cancelled_response_ids == {"resp_1"} and b.state.cancelled_response_ids == set()
    await b.event("response.output_audio.delta", delta="BBBB", response_id="resp_1")
    await a.event("response.output_audio.delta", delta="AAAA", response_id="resp_1")
    assert "BBBB" in b.audio() and "AAAA" not in a.audio()
    assert b.cancels() == [] and not b.cleared()
    await adapter.cancel_pending_tasks(a.state)


# --- the interrupted turn is not lost ---------------------------------------------------------------------------------


async def test_the_interrupted_answer_is_recorded_with_its_question(
    monkeypatch: pytest.MonkeyPatch, base: list[tuple[str, Any]]
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True)
    call = Call()
    await call.agent_starts_answering("resp_1", question="franchise ke baare mein batao")
    await _interrupt(call)
    await adapter._cut_now(call.agent, call.phone, call.state, call.log, "again")  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert base == [("Dava India franchise mein investment", "franchise ke baare mein batao")]  # once
    await adapter.cancel_pending_tasks(call.state)


async def test_without_the_flag_the_interrupted_turn_is_not_recorded_as_before(
    base: list[tuple[str, Any]],
) -> None:
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await asyncio.sleep(0.05)
    assert base == []


# --- natural turn-taking -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["haan", "hmm", "okay", "theek hai", "achha ji"])
def test_acknowledgements_never_count_as_interruptions_in_either_mode(text: str) -> None:
    assert turn_controller.classify_partial(text) == "filler"
    assert turn_controller.classify_partial(text, smart=True) == "filler"


@pytest.mark.parametrize(
    "text",
    ["Ismein profit kitna hoga?", "kya investment lagega", "wait, what is the cost", "aap kaun ho"],
)
def test_questions_are_real_turns_in_both_modes(text: str) -> None:
    assert turn_controller.classify_partial(text) == "real"
    assert turn_controller.classify_partial(text, smart=True) == "real"


def test_corrections_are_recognised_only_in_smart_mode() -> None:
    for text in ("galat hai", "sorry I meant tomorrow", "गलत है", "maaf kijiye"):
        assert turn_controller.classify_partial(text) in ("unsure", "real")  # legacy: unchanged
        assert turn_controller.classify_partial(text, smart=True) == "real"
    assert turn_controller.classify_partial("galat hai") == "unsure"  # the legacy verdict, pinned


def test_background_noise_is_not_an_interruption_in_smart_mode() -> None:
    for noise in ("...", "?!", "aaaa", "mmmm", "-"):
        assert turn_controller.is_noise(noise)
        assert turn_controller.classify_partial(noise, smart=True) == "filler"
    assert turn_controller.classify_partial("...") == "unsure"  # legacy verdict unchanged
    assert not turn_controller.is_noise("")  # nothing transcribed yet is not noise
    assert not turn_controller.is_noise("haan")


async def test_a_pause_mid_sentence_is_waited_out_only_in_smart_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(smart: bool) -> tuple[int, int]:
        _flags(monkeypatch, voice_smart_turn_taking=smart)
        call = Call()
        call.state.live = type("Live", (), {"healthy": True})()
        call.state.utterance_text = "haan mera matlab..."
        await call.event("input_audio_buffer.speech_stopped")
        await call.event("input_audio_buffer.committed")
        await asyncio.sleep(0.05)
        early = len(call.replies())
        await adapter.handle_live_delta(" ye hai ki plan kya hai.", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]
        await asyncio.sleep(0.05)
        return early, len(call.replies())

    assert await run(smart=False) == (1, 1)  # legacy: answers the unfinished sentence at once
    assert await run(smart=True) == (0, 1)  # held until the sentence is finished, then answered once


# --- prioritising the interruption -------------------------------------------------------------------------------------------


async def test_a_question_interruption_is_answered_first_without_inventing_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_smart_turn_taking=True)
    call = Call()
    call.state.live = type("Live", (), {"healthy": True})()
    await call.agent_starts_answering("resp_1", question="franchise mein kitna investment")
    await call.event("input_audio_buffer.speech_started")
    await adapter.handle_live_delta("Ismein profit kitna hoga?", call.phone, call.agent, call.state, call.log)  # type: ignore[arg-type]

    assert call.cleared()  # the previous scripted answer stops
    assert call.cancels() == [{"type": "response.cancel", "response_id": "resp_1"}]
    await _caller_finishes_turn(call, "Ismein profit kitna hoga?")
    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await asyncio.sleep(0.1)

    (reply,) = call.replies()
    instructions = reply["response"]["instructions"]
    assert instructions.startswith("BASE INSTRUCTIONS")  # the call's own instructions are kept
    assert "interrupted you" in instructions and "never invent" in instructions
    assert "profits" in instructions  # explicit: no profitability claims
    assert not call.state.priority_reply  # consumed: the next ordinary reply is untouched
    await adapter.cancel_pending_tasks(call.state)


async def test_priority_is_off_by_default_and_not_leaked_to_ordinary_replies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    await _caller_finishes_turn(call, "profit kitna")
    await call.event("response.done", response={"id": "resp_1"})
    await asyncio.sleep(0.1)
    (reply,) = call.replies()
    assert reply == {"type": "response.create"}  # byte-for-byte the legacy payload

    # With the flag on, an ORDINARY turn (no interruption) gets no priority text.
    _flags(monkeypatch, voice_smart_turn_taking=True)
    plain = Call()
    plain.state.reply_pending = True
    await adapter._fire_pending_reply(plain.agent, plain.state)  # type: ignore[arg-type]
    assert plain.replies() == [{"type": "response.create"}]


async def test_a_filler_after_a_cut_cancels_the_priority_so_it_cannot_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    call = Call()
    call.state.priority_reply = True
    call.state.reply_pending = True
    adapter._drop_filler_reply(call.state)
    assert not call.state.priority_reply and not call.state.reply_pending


async def test_priority_text_is_added_to_the_decision_layers_instructions() -> None:
    state = adapter.CallState(user_id=USER, conversation_id=uuid.uuid4(), org_id=ORG_A, base_instructions="BASE")
    merged = adapter._with_priority({"instructions": "BASE\n\nDECISION FOR THIS TURN: x", "tool_choice": "none"}, state)
    assert merged["instructions"].startswith("BASE\n\nDECISION FOR THIS TURN: x")
    assert merged["instructions"].endswith(adapter.INTERRUPT_PRIORITY_INSTRUCTION)
    assert merged["tool_choice"] == "none"
    assert adapter._with_priority(None, state)["instructions"].startswith("BASE")


# --- compatibility with Phase 1 prewarm ------------------------------------------------------------------------------------------


async def test_prewarm_and_safe_cancel_work_together(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_elevenlabs_prewarm=True)
    call = Call(tts_provider="elevenlabs")
    await call.event("response.created", response={"id": "resp_1"})
    await asyncio.sleep(0.02)
    warmed = FakeSession.instances[0]
    call.state.playback_end = time.monotonic() + 5
    call.state.resp_text = "x"

    await _interrupt(call)  # cut before any text arrived: the idle prewarm must go
    assert warmed.closed and call.state.eleven_prewarm_task is None
    assert call.cancels() == [{"type": "response.cancel", "response_id": "resp_1"}]

    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await call.event("response.created", response={"id": "resp_2"})  # the answer to the interruption
    await asyncio.sleep(0.02)
    assert len(FakeSession.instances) == 2 and not FakeSession.instances[1].closed
    await call.event("response.text.delta", delta="Jawab", response_id="resp_2")
    assert FakeSession.instances[1].sent == ["Jawab"] and call.state.eleven_session is FakeSession.instances[1]
    await adapter.cancel_pending_tasks(call.state)


def test_instant_ack_is_off_by_default_and_only_for_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.channels.voice import adapter
    from apps.api.config import Settings

    assert Settings.model_fields["voice_instant_ack"].default is False
    monkeypatch.setattr(adapter.settings, "voice_instant_ack", False)
    assert adapter._ack_voice(types.SimpleNamespace(tts_provider="elevenlabs", voice=None)) is None
    monkeypatch.setattr(adapter.settings, "voice_instant_ack", True)
    assert adapter._ack_voice(types.SimpleNamespace(tts_provider="openai", voice=None)) is None
    voice, model, vs = adapter._ack_voice(types.SimpleNamespace(tts_provider="elevenlabs", voice=None))
    assert voice and model and vs["similarity_boost"] == 0.75


@pytest.mark.parametrize(
    "text",
    ["hmm", "hmmm", "ok", "okay", "thik hai", "theek hai", "acha", "achha", "right", "haan", "ji",
     "अच्छा", "हाँ", "जी", "haan ji", "जी हाँ", "ठीक है"],
)
def test_backchannels_never_count_as_a_real_turn(text: str) -> None:
    assert turn_controller.is_backchannel(text)
    assert turn_controller.classify_partial(text) == "filler"
    assert turn_controller.is_short_burst(text)


def test_a_backchannel_with_a_real_ask_is_still_a_turn() -> None:
    assert turn_controller.classify_partial("haan lekin price kya hai") == "real"
    assert not turn_controller.is_short_burst("haan lekin price kya hai")


def test_filler_over_the_agent_is_marked_once_as_non_interruptive() -> None:
    events: list[str] = []
    state = types.SimpleNamespace(
        caller_speaking=False, backchannel_marked=False,
        latency=types.SimpleNamespace(event=lambda kind, **kw: events.append(kind)),
        live_stats=types.SimpleNamespace(filler_ignored=0),
        reply_pending=True, restate_next=False, interrupted_mid_response=True, priority_reply=False,
    )
    adapter._drop_filler_reply(state)
    adapter._drop_filler_reply(state)
    assert events == ["non_interruptive_backchannel"]
    assert state.reply_pending is False


def test_candidate_backchannel_is_short_quick_and_not_an_ask() -> None:
    cand = turn_controller.is_candidate_backchannel
    assert cand("sahi hai ji") and cand("arre wah", 0.6) and cand("achha ji", 0.9)
    assert not cand("sahi hai ji", 1.2)  # spoke for over a second
    assert not cand("haan ek baat batao")  # four words
    assert not cand("price kya hai") and not cand("ruko")  # an ask / a stop
    assert not cand("")


async def test_judge_leans_filler_only_for_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.core import llm

    seen: list[str] = []

    async def fake_chat(messages: Any, **kw: Any) -> Any:
        seen.append(messages[0]["content"])
        return types.SimpleNamespace(content='{"label": "FILLER", "confidence": 0.9}', tokens_in=1, tokens_out=1)

    monkeypatch.setattr(llm, "chat_completion", fake_chat)
    await turn_controller.judge_turn("sahi hai ji", "k", lean_filler=True)
    await turn_controller.judge_turn("sahi hai ji", "k")
    assert "When unsure, choose FILLER" in seen[0]
    assert "When unsure, choose FILLER" not in seen[1]
    assert "choose the other label" in seen[1]


async def test_fallback_keeps_talking_for_an_uncertain_short_snippet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "LIVE_FALLBACK_CUT_SECONDS", 0)
    monkeypatch.setattr(adapter.settings, "voice_smart_turn_taking", True)
    cut: list[str] = []

    async def fake_cut(*a: Any, **k: Any) -> None:
        cut.append("cut")

    monkeypatch.setattr(adapter, "_cut_now", fake_cut)
    state = types.SimpleNamespace(
        caller_speaking=True, live_llm_said_filler=False, utterance_text="sahi hai ji",
        t_speech_started=time.monotonic(), live_stats=types.SimpleNamespace(fallback_filler_kept=0),
    )
    await adapter._live_fallback_cut(None, None, state, adapter.structlog.get_logger() if hasattr(adapter, "structlog") else None)
    assert cut == [] and state.live_stats.fallback_filler_kept == 1
    state.utterance_text = "mujhe ek baat batao"  # four words: a real turn
    await adapter._live_fallback_cut(None, None, state, None)
    assert cut == ["cut"]
