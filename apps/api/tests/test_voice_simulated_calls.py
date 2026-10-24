"""Simulated phone calls (blueprint sections 17 and 24).

A real call needs a phone line; these tests drive the *real* voice adapter with the
same OpenAI Realtime events a live call produces (speech started / stopped, audio
deltas, committed turns, transcripts, response done) through fake sockets, and assert
what the caller would hear and what the engine would do. They cover:

* barge-in: the caller interrupts, audio is cut, the interruption becomes the next turn;
* filler words ("ok") never trigger an answer;
* the greeting is not cut by the callee's first "hello?";
* silence: a gentle check, a second check, then the connection-issue state; a caller's
  voice resets it; a short pause does nothing;
* transcription errors: garbled text never triggers an action;
* shadow mode: analysed and logged, never spoken, nothing executed;
* latency: a slow decision never delays the reply beyond its budget;
* the live-voice "when unsure" options (clarify / hand to a person / script).

Nothing here touches the network, a real database or a real phone.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.core.conversation import builder, shadow, voice_decision
from apps.api.core.conversation.config import DEFAULT_SCENARIOS
from apps.api.core.conversation.voice_decision import apply_voice_plan, build_instruction, compute_voice_plan
from apps.api.core.llm import ChatResult
from apps.api.db.models import Appointment, Conversation, Org, User
from apps.api.db.models.conversation_engine import ConversationTurn

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


# --- the simulated call ------------------------------------------------------------------------


class _ProviderSocket:
    """The telephony side: what the caller's phone would receive."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _RealtimeSocket:
    """The OpenAI Realtime side: what the agent is told to do."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    warning = info


class Call:
    def __init__(self, **state: Any) -> None:
        self.state = adapter.CallState(
            user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE"
        )
        for key, value in state.items():
            setattr(self.state, key, value)
        self.phone = _ProviderSocket()
        self.agent = _RealtimeSocket()

    async def event(self, etype: str, **fields: Any) -> None:
        await adapter.handle_openai_event(
            {"type": etype, **fields}, self.phone, self.agent, self.state, _Log()  # type: ignore[arg-type]
        )

    def agent_replies(self) -> list[dict[str, Any]]:
        return [m for m in self.agent.sent if m["type"] == "response.create"]

    def audio_cleared(self) -> bool:
        return any(m.get("event") == "clearAudio" for m in self.phone.sent)

    def cancels(self) -> list[dict[str, Any]]:
        return [m for m in self.agent.sent if m["type"] == "response.cancel"]


@pytest.fixture(autouse=True)
def _reset_caches() -> Any:
    voice_decision.reset_flag_cache()
    yield
    voice_decision.reset_flag_cache()


@pytest.fixture
def no_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """For tests that drive the adapter with scripted events: no decision task may reach a
    database, and the interruption timers are shortened so the test does not wait seconds."""
    monkeypatch.setattr(settings, "conversation_voice_live", False)

    async def no_plan(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "compute_voice_plan", no_plan)
    monkeypatch.setattr(adapter, "EARLY_CUT_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "LIVE_FALLBACK_CUT_SECONDS", 0.05)


def _agent_is_speaking(call: Call, seconds: float = 5.0) -> None:
    call.state.response_active = True
    call.state.playback_end = time.monotonic() + seconds


# --- barge-in ---------------------------------------------------------------------------------------


@pytest.mark.usefixtures("no_decisions")
async def test_barge_in_cuts_the_audio_and_the_interruption_becomes_the_next_turn() -> None:
    call = Call()
    _agent_is_speaking(call)

    await call.event("input_audio_buffer.speech_started")  # the caller talks over the agent
    assert call.state.interrupted_mid_response and call.state.caller_speaking
    await asyncio.sleep(0.25)  # they keep talking past the early-cut threshold
    assert call.audio_cleared()  # queued speech is dropped at the provider: the caller hears silence
    assert call.state.suppress_response_audio
    # Deliberate design: never cancel a streaming response (it once closed the whole session).
    assert call.cancels() == []

    # The rest of the muted answer must not reach the caller.
    before = len(call.phone.sent)
    await call.event("response.audio.delta", delta="AAAA")
    assert len(call.phone.sent) == before

    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript="price kitna hai")
    assert call.agent_replies() == []  # not yet: the muted answer is still finishing
    await call.event("response.done")
    await asyncio.sleep(0.1)
    assert len(call.agent_replies()) == 1  # the interruption is answered as the new turn


@pytest.mark.usefixtures("no_decisions")
async def test_a_short_sound_is_not_a_barge_in() -> None:
    call = Call()
    _agent_is_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")  # a cough: over before the threshold
    await asyncio.sleep(0.25)
    assert not call.audio_cleared()  # the agent keeps talking


@pytest.mark.usefixtures("no_decisions")
async def test_filler_while_the_agent_talks_never_triggers_an_answer() -> None:
    call = Call()
    _agent_is_speaking(call)
    await call.event("input_audio_buffer.speech_started")
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await call.event("conversation.item.input_audio_transcription.completed", transcript="ok")
    assert not call.state.reply_pending  # "ok" is a back-channel, not a question
    await call.event("response.done")
    await asyncio.sleep(0.1)
    assert call.agent_replies() == []


@pytest.mark.usefixtures("no_decisions")
async def test_the_callees_first_hello_does_not_cut_the_greeting() -> None:
    call = Call()
    _agent_is_speaking(call)
    call.state.greeting_guard_until = time.monotonic() + 5  # set when the opening greeting fires
    await call.event("input_audio_buffer.speech_started")  # "hello?" the instant they pick up
    await asyncio.sleep(0.25)
    assert not call.state.caller_speaking and not call.audio_cleared()


# --- silence ---------------------------------------------------------------------------------------------


def _silence_settings(monkeypatch: pytest.MonkeyPatch, *, check_after: float, max_checks: int) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_silence_check_seconds", check_after)
    monkeypatch.setattr(settings, "conversation_silence_max_checks", max_checks)
    voice_decision.set_cached_voice_mode(ORG_ID, "live")


@pytest.fixture
def silence_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    logged: list[str] = []

    async def fake_log(org_id: Any, user_id: Any, conversation_id: Any, *, kind: str) -> None:
        logged.append(kind)

    monkeypatch.setattr(voice_decision, "log_silence_event", fake_log)
    return logged


@pytest.mark.usefixtures("no_decisions")
async def test_silence_gets_a_gentle_check_then_a_polite_goodbye(
    monkeypatch: pytest.MonkeyPatch, silence_log: list[str]
) -> None:
    _silence_settings(monkeypatch, check_after=0.1, max_checks=1)
    call = Call()
    await call.event("response.done")  # the agent finishes speaking -> the silence clock starts
    await asyncio.sleep(1.3)
    assert silence_log == ["check"]
    check = call.agent_replies()[-1]["response"]
    assert check["tool_choice"] == "none" and "main yahin hoon" in check["instructions"]

    await call.event("response.done")  # the check was spoken; the caller is still silent
    await asyncio.sleep(1.3)
    assert silence_log == ["check", "connection_issue"]
    assert "goodbye" in call.agent_replies()[-1]["response"]["instructions"]
    await call.event("response.done")
    await asyncio.sleep(1.2)
    assert silence_log == ["check", "connection_issue"]  # flagged once, never repeated


@pytest.mark.usefixtures("no_decisions")
async def test_a_short_pause_while_the_caller_thinks_does_nothing(
    monkeypatch: pytest.MonkeyPatch, silence_log: list[str]
) -> None:
    _silence_settings(monkeypatch, check_after=5.0, max_checks=2)
    call = Call()
    await call.event("response.done")
    await asyncio.sleep(1.3)
    assert silence_log == [] and call.agent_replies() == []


@pytest.mark.usefixtures("no_decisions")
async def test_the_callers_voice_resets_the_silence_counter(
    monkeypatch: pytest.MonkeyPatch, silence_log: list[str]
) -> None:
    _silence_settings(monkeypatch, check_after=0.1, max_checks=2)
    call = Call()
    await call.event("response.done")
    await asyncio.sleep(1.3)
    assert silence_log == ["check"] and call.state.silence.checks == 1
    await call.event("input_audio_buffer.speech_started")  # "haan, main yahin hoon"
    assert call.state.silence.checks == 0
    await call.event("input_audio_buffer.speech_stopped")
    await asyncio.sleep(1.3)
    assert silence_log == ["check"]  # nothing more until the agent speaks and the line goes quiet again


@pytest.mark.usefixtures("no_decisions")
async def test_silence_handling_is_off_for_orgs_not_on_live_voice(
    monkeypatch: pytest.MonkeyPatch, silence_log: list[str]
) -> None:
    _silence_settings(monkeypatch, check_after=0.1, max_checks=1)
    voice_decision.set_cached_voice_mode(ORG_ID, "shadow")
    call = Call()
    await call.event("response.done")
    await asyncio.sleep(1.3)
    assert silence_log == [] and call.state.silence is None and call.agent_replies() == []


# --- decisions on a live call: transcription errors, latency, shadow, "when unsure" ----------------------


@pytest.fixture
def session_factory(test_engine, monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", factory)
    monkeypatch.setattr(shadow, "AsyncSessionLocal", factory)
    return factory


async def _seed(factory: async_sessionmaker[AsyncSession], *, mode: str = "human_conversation", voice: str = "live") -> uuid.UUID:
    async with factory() as db:
        db.add(Org(id=ORG_ID, name="Test Org", conversation_mode=mode, conversation_voice_mode=voice))
        db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
        conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="voice")
        db.add(conversation)
        await db.commit()
        return conversation.id


async def _publish(factory: async_sessionmaker[AsyncSession], action: str) -> None:
    async with factory() as db:
        scenarios = [s.to_json() for s in DEFAULT_SCENARIOS]
        await builder.save_draft(db, ORG_ID, "t", {"scenarios": scenarios, "settings": {"low_confidence_action": action}}, None)
        await builder.publish_draft(db, ORG_ID, "t", None)


def _unsure_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        return ChatResult(
            content=json.dumps({"scenario": "interested", "intent": "x", "emotion": "neutral",
                                "confidence": 0.2, "facts": {}, "action_id": "none"}),
            tokens_in=1, tokens_out=1,
        )

    monkeypatch.setattr(voice_decision, "_default_llm", lambda api_key: call)


@pytest.mark.parametrize("garbled", ["asdkj qwe zxcv", "...", "mmm uh hmm", "बहुत शोर आ रहा है ऊऊऊ"])
async def test_garbled_transcripts_never_trigger_an_action(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, garbled: str
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    conversation_id = await _seed(session_factory)
    _unsure_model(monkeypatch)
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, garbled)
    assert plan is None  # default setting: the call carries on exactly as it does today
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Appointment)) == 0


async def test_when_unsure_the_agent_can_ask_the_caller_to_say_it_again(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    conversation_id = await _seed(session_factory)
    await _publish(session_factory, "clarify")
    _unsure_model(monkeypatch)
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "asdkj qwe zxcv")
    assert plan is not None and plan.decision.reply_kind == "clarify" and plan.decision.action_id == "none"
    text = build_instruction(plan, action_result=None)
    assert "say a little more" in text and "do not guess" in text.lower()
    recorder = _Recorder()
    reply = await apply_voice_plan(
        plan, org_id=ORG_ID, user_id=USER_ID, conversation_id=conversation_id,
        text="asdkj qwe zxcv", dispatch=recorder, done_actions=set(),
    )
    assert reply is not None and reply.tool_choice_none is False and recorder.calls == []


async def test_when_unsure_the_agent_can_hand_the_call_to_a_person_exactly_once(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    conversation_id = await _seed(session_factory)
    await _publish(session_factory, "handoff")
    _unsure_model(monkeypatch)
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "asdkj qwe zxcv")
    assert plan is not None and plan.decision.action_id == "transfer_to_human"
    recorder, done = _Recorder(), set()
    for _ in range(2):  # the same decision applied twice must still run the action once
        reply = await apply_voice_plan(
            plan, org_id=ORG_ID, user_id=USER_ID, conversation_id=conversation_id,
            text="asdkj qwe zxcv", dispatch=recorder, done_actions=done,
        )
        assert reply is not None
    assert [name for name, _ in recorder.calls] == ["transfer_to_human"]


async def test_smart_script_never_improvises_on_a_call_when_unsure(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    conversation_id = await _seed(session_factory, mode="smart_script")
    await _publish(session_factory, "clarify")
    _unsure_model(monkeypatch)
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "asdkj qwe zxcv") is None


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return {"status": "ok"}


async def test_shadow_mode_analyses_and_logs_but_never_speaks_or_acts(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_shadow", True)
    conversation_id = await _seed(session_factory, voice="shadow")

    shadow.schedule_voice_shadow(ORG_ID, USER_ID, conversation_id, "I am busy, call me tomorrow at 5 pm")
    shadow.schedule_voice_shadow(ORG_ID, USER_ID, conversation_id, "(voice)")  # placeholder: ignored
    await asyncio.gather(*list(shadow._tasks))

    async with session_factory() as db:
        turns = list((await db.execute(select(ConversationTurn))).scalars())
        appointments = await db.scalar(select(func.count()).select_from(Appointment))
    assert len(turns) == 1
    turn = turns[0]
    assert turn.mode == "shadow" and turn.channel == "voice"
    assert turn.scenario == "busy_callback" and turn.action_id == "request_callback"  # what it WOULD do
    assert turn.action_result is None and appointments == 0  # ...but nothing was executed

    # And the call itself is untouched: the reply the agent is asked for is the plain, unsteered one.
    voice_decision.set_cached_voice_mode(ORG_ID, "shadow")
    call = Call()
    call.state.pending_user_transcript = "I am busy, call me tomorrow at 5 pm"
    await adapter._fire_pending_reply(call.agent, call.state)
    assert call.agent.sent == [{"type": "response.create"}]


async def test_a_slow_decision_never_delays_the_reply_past_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 0.3)
    voice_decision.set_cached_voice_mode(ORG_ID, "live")

    async def very_slow(*args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(voice_decision, "compute_voice_plan", very_slow)
    call = Call()
    call.state.pending_user_transcript = "kal 5 baje call karna"
    adapter._start_voice_decision(call.state, call.state.pending_user_transcript)
    started = time.monotonic()
    await adapter._fire_pending_reply(call.agent, call.state)
    elapsed = time.monotonic() - started
    assert elapsed < 0.3 + 0.3  # the budget plus scheduling slack, never the 5 s the decision wanted
    assert call.agent.sent == [{"type": "response.create"}]  # plain reply: the call carried on as before


async def test_a_clear_rules_decision_adds_no_noticeable_delay(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    conversation_id = await _seed(session_factory)

    async def no_model(messages: list[dict[str, Any]]) -> ChatResult:
        raise AssertionError("a clear case must not call the model")

    monkeypatch.setattr(voice_decision, "_default_llm", lambda api_key: no_model)
    started = time.monotonic()
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "I am busy, call me tomorrow at 5 pm")
    elapsed = time.monotonic() - started
    assert plan is not None and plan.decision.action_id == "request_callback"
    assert elapsed < 0.5  # rules decide in milliseconds; the rest is a database read
