"""Decision Layer on live voice calls.

Two halves: the decision module (core/conversation/voice_decision.py) against
the in-memory DB, and the adapter hook (channels/voice/adapter.py) against a
fake realtime socket that records the exact messages sent to the model.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.core.conversation.memory import load_memory
from apps.api.core.conversation.voice_decision import VoiceReply, apply_voice_plan, compute_voice_plan
from apps.api.core.llm import ChatResult
from apps.api.db.models import Conversation, Org, User
from apps.api.db.models.conversation_engine import ConversationTurn, KnowledgeItem

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    voice_decision.reset_flag_cache()
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 1.0)
    yield
    voice_decision.reset_flag_cache()


@pytest.fixture
def live_cache() -> None:
    """The call path only waits for an org it already knows is on live voice."""
    voice_decision.set_cached_voice_mode(ORG_ID, "live")


@pytest.fixture
def session_factory(test_engine, monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", factory)
    return factory


async def _seed(factory: async_sessionmaker[AsyncSession], *, voice_mode: str = "live") -> uuid.UUID:
    async with factory() as db:
        db.add(
            Org(
                id=ORG_ID,
                name="Test Org",
                conversation_mode="human_conversation",
                conversation_voice_mode=voice_mode,
            )
        )
        db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
        conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="voice")
        db.add(conversation)
        await db.commit()
        return conversation.id


def _llm(content: str | None, delay: float = 0.0):
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        if delay:
            await asyncio.sleep(delay)
        return ChatResult(content=content, tokens_in=5, tokens_out=3)

    return lambda api_key: call


class _Recorder:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result or {"status": "ok"}

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return self.result


async def _turns(factory: async_sessionmaker[AsyncSession]) -> list[ConversationTurn]:
    async with factory() as db:
        return list((await db.execute(select(ConversationTurn).order_by(ConversationTurn.created_at))).scalars())


# --- switches ---------------------------------------------------------------------


async def test_not_live_means_nothing_happens(session_factory: async_sessionmaker[AsyncSession]) -> None:
    conversation_id = await _seed(session_factory, voice_mode="shadow")
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "kal 5 baje call karna") is None
    assert await _turns(session_factory) == []


async def test_off_means_nothing_happens(session_factory: async_sessionmaker[AsyncSession]) -> None:
    conversation_id = await _seed(session_factory, voice_mode="off")
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None


async def test_global_kill_switch_beats_the_org_setting(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _seed(session_factory)
    monkeypatch.setattr(settings, "conversation_voice_live", False)
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None


async def test_strict_script_org_is_never_live(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as db:
        db.add(Org(id=ORG_ID, name="Strict", conversation_mode="strict_script", conversation_voice_mode="live"))
        db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="T"))
        conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="voice")
        db.add(conversation)
        await db.commit()
        conversation_id = conversation.id
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None


async def test_switch_change_is_picked_up_after_the_cache_expires(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = await _seed(session_factory, voice_mode="shadow")
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None
    async with session_factory() as db:
        org = await db.get(Org, ORG_ID)
        org.conversation_voice_mode = "live"
        await db.commit()
    # Still cached as "not live" for a few seconds ...
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None
    # ... and picked up as soon as the cache is cleared.
    voice_decision.reset_flag_cache()
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is not None


# --- deciding and applying ----------------------------------------------------------


async def test_callback_is_decided_then_executed_once_and_logged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = await _seed(session_factory)
    text = "abhi busy hoon, kal shaam 5 baje call karna"
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, text)
    assert plan is not None
    assert plan.understanding.scenario == "busy_callback"
    assert plan.decision.action_id == "request_callback"
    # Deciding alone changes nothing yet.
    assert await _turns(session_factory) == []

    dispatch = _Recorder()
    done: set[str] = set()
    reply = await apply_voice_plan(
        plan,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation_id,
        text=text,
        dispatch=dispatch,
        done_actions=done,
    )
    assert reply is not None
    assert reply.tool_choice_none is True  # the model must not also call the tool
    assert "callback is now scheduled" in reply.instruction
    assert "17:00" in reply.instruction
    assert [c[0] for c in dispatch.calls] == ["request_callback"]

    (turn,) = await _turns(session_factory)
    assert turn.mode == "live" and turn.channel == "voice"
    assert turn.action_id == "request_callback" and turn.fallback_reason is None
    async with session_factory() as db:
        assert (await load_memory(db, ORG_ID, USER_ID)).get("callback_time", "").endswith("17:00")

    # The caller repeats themselves: the action does not run a second time.
    again = await apply_voice_plan(
        plan,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation_id,
        text=text,
        dispatch=dispatch,
        done_actions=done,
    )
    assert again is not None and again.tool_choice_none is False
    assert len(dispatch.calls) == 1


async def test_action_failure_returns_nothing_so_the_call_continues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = await _seed(session_factory)
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "not interested, stop calling")
    assert plan is not None and plan.decision.action_id == "mark_not_interested"
    reply = await apply_voice_plan(
        plan,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation_id,
        text="not interested, stop calling",
        dispatch=_Recorder({"status": "error", "reason": "boom"}),
        done_actions=set(),
    )
    assert reply is None
    (turn,) = await _turns(session_factory)
    assert turn.fallback_reason == "action_failed"


async def test_knowledge_answer_instruction_contains_only_approved_facts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = await _seed(session_factory)
    async with session_factory() as db:
        db.add(
            KnowledgeItem(
                org_id=ORG_ID,
                kind="pricing",
                title="Premium plan price",
                body="The Premium plan costs Rs 1,00,000.",
                keywords="premium price plan",
            )
        )
        await db.commit()
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "premium plan ka price kya hai")
    assert plan is not None and plan.decision.reply_kind == "answer"
    reply = await apply_voice_plan(
        plan,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation_id,
        text="premium plan ka price kya hai",
        dispatch=_Recorder(),
        done_actions=set(),
    )
    assert reply is not None and reply.tool_choice_none is False
    assert "ONLY these approved facts" in reply.instruction
    assert "The Premium plan costs Rs 1,00,000." in reply.instruction
    assert "Hinglish" in reply.instruction  # replies in the caller's language


async def test_unknown_question_is_handed_to_a_human_not_guessed(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _seed(session_factory)
    monkeypatch.setattr(
        voice_decision,
        "_default_llm",
        _llm(json.dumps({"scenario": "unknown_question", "intent": "x", "emotion": "neutral",
                         "language": "en", "confidence": 0.85})),
    )
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "do you deliver to Pune")
    assert plan is not None and plan.decision.action_id == "transfer_to_human"
    dispatch = _Recorder()
    reply = await apply_voice_plan(
        plan, org_id=ORG_ID, user_id=USER_ID, conversation_id=conversation_id,
        text="do you deliver to Pune", dispatch=dispatch, done_actions=set(),
    )
    assert reply is not None and "Do not guess" in reply.instruction
    assert [c[0] for c in dispatch.calls] == ["transfer_to_human"]


@pytest.mark.parametrize(
    ("llm", "reason"),
    [
        (_llm("not json"), "invalid_output"),
        (_llm(json.dumps({"scenario": "interested", "intent": "x", "emotion": "neutral",
                          "language": "en", "confidence": 0.2})), "low_confidence"),
        (_llm("{}", delay=5.0), "timeout"),
    ],
)
async def test_doubt_means_no_decision_and_a_logged_fallback(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    llm: Any,
    reason: str,
) -> None:
    conversation_id = await _seed(session_factory)
    monkeypatch.setattr(voice_decision, "_default_llm", llm)
    monkeypatch.setattr(settings, "conversation_voice_llm_timeout_seconds", 0.1)
    plan = await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "do you deliver to Pune")
    assert plan is None
    (turn,) = await _turns(session_factory)
    assert turn.fallback_reason == reason and turn.mode == "live"


async def test_internal_error_never_escapes(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation_id = await _seed(session_factory)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(voice_decision, "decide", boom)
    assert await compute_voice_plan(ORG_ID, USER_ID, conversation_id, "stop calling me") is None


# --- adapter hook: exactly what is sent to the model --------------------------------


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


def _state(**kw: Any) -> adapter.CallState:
    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE PROMPT"
    )
    state.pending_user_transcript = "kal 5 baje call karna"
    for key, value in kw.items():
        setattr(state, key, value)
    return state


def _task_returning(value: Any, delay: float = 0.0) -> "asyncio.Task[Any]":
    async def run() -> Any:
        if delay:
            await asyncio.sleep(delay)
        return value

    return asyncio.get_running_loop().create_task(run())


async def test_without_a_decision_the_reply_is_exactly_what_it_always_was() -> None:
    ws, state = _FakeSocket(), _state()
    await adapter._fire_pending_reply(ws, state)
    assert ws.sent == [{"type": "response.create"}]


@pytest.mark.usefixtures("live_cache")
async def test_a_ready_decision_steers_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object())

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        return VoiceReply(instruction="DECISION FOR THIS TURN: confirm the callback", tool_choice_none=True)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    await adapter._fire_pending_reply(ws, state)

    (message,) = ws.sent
    assert message["type"] == "response.create"
    assert message["response"]["instructions"].startswith("BASE PROMPT\n\n")
    assert "confirm the callback" in message["response"]["instructions"]
    assert message["response"]["tool_choice"] == "none"
    assert state.voice_decision_task is None  # consumed


@pytest.mark.usefixtures("live_cache")
async def test_no_tool_choice_override_when_no_action_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object())

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        return VoiceReply(instruction="DECISION FOR THIS TURN: answer", tool_choice_none=False)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    await adapter._fire_pending_reply(ws, state)
    assert "tool_choice" not in ws.sent[0]["response"]


@pytest.mark.usefixtures("live_cache")
async def test_a_late_decision_is_dropped_and_never_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 0.05)
    applied: list[Any] = []

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        applied.append(plan)
        return VoiceReply(instruction="x", tool_choice_none=True)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    task = _task_returning(object(), delay=2.0)
    state.voice_decision_task = task
    await adapter._fire_pending_reply(ws, state)

    assert ws.sent == [{"type": "response.create"}]
    assert applied == []  # a dropped decision must never run an action
    await asyncio.sleep(0)
    assert task.cancelled()


@pytest.mark.usefixtures("live_cache")
async def test_a_stale_decision_for_an_older_utterance_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        called.append(plan)
        return VoiceReply(instruction="x", tool_choice_none=False)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    ws = _FakeSocket()
    state = _state(voice_decision_text="an older sentence")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object())
    await adapter._fire_pending_reply(ws, state)
    assert ws.sent == [{"type": "response.create"}] and called == []


@pytest.mark.usefixtures("live_cache")
async def test_no_plan_or_a_failing_decision_leaves_the_reply_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for task_value in (None, RuntimeError("boom")):
        ws = _FakeSocket()
        state = _state(voice_decision_text="kal 5 baje call karna")
        state.voice_decision_started = time.monotonic()

        async def run(value: Any = task_value) -> Any:
            if isinstance(value, Exception):
                raise value
            return value

        state.voice_decision_task = asyncio.get_running_loop().create_task(run())
        await adapter._fire_pending_reply(ws, state)
        assert ws.sent == [{"type": "response.create"}]


@pytest.mark.usefixtures("live_cache")
async def test_apply_returning_nothing_leaves_the_reply_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_apply(plan: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object())
    await adapter._fire_pending_reply(ws, state)
    assert ws.sent == [{"type": "response.create"}]


@pytest.mark.usefixtures("live_cache")
async def test_the_restate_branch_never_consults_the_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        called.append(plan)
        return VoiceReply(instruction="x", tool_choice_none=False)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    ws = _FakeSocket()
    state = _state(restate_next=True, voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object())
    await adapter._fire_pending_reply(ws, state)
    assert called == []
    assert "response.create" == ws.sent[0]["type"]
    assert "instructions" in ws.sent[0]["response"] and "DECISION" not in ws.sent[0]["response"]["instructions"]


async def test_starting_a_decision_respects_the_global_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    monkeypatch.setattr(settings, "conversation_voice_live", False)
    adapter._start_voice_decision(state, "hello")
    assert state.voice_decision_task is None

    monkeypatch.setattr(settings, "conversation_voice_live", True)

    async def fake_compute(*args: Any) -> None:
        return None

    monkeypatch.setattr(voice_decision, "compute_voice_plan", fake_compute)
    adapter._start_voice_decision(state, "hello")
    first = state.voice_decision_task
    assert first is not None and state.voice_decision_text == "hello"
    # A newer turn replaces the older decision.
    adapter._start_voice_decision(state, "hello again")
    assert state.voice_decision_task is not first
    await asyncio.sleep(0)
    assert first.cancelled() or first.done()


# --- existing orgs are never slowed down or touched --------------------------------------


@pytest.mark.parametrize("mode", ["none", "off", "shadow"])
async def test_a_reply_never_waits_for_an_org_that_is_not_live(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    voice_decision.set_cached_voice_mode(ORG_ID, mode)
    applied: list[Any] = []

    async def fake_apply(plan: Any, **kwargs: Any) -> VoiceReply:
        applied.append(plan)
        return VoiceReply(instruction="x", tool_choice_none=True)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object(), delay=5.0)  # would stall for 5 s
    started = time.monotonic()
    await adapter._fire_pending_reply(ws, state)
    assert time.monotonic() - started < 0.3  # sent at once, as it always has been
    assert ws.sent == [{"type": "response.create"}]
    assert applied == []


async def test_a_reply_for_an_org_not_seen_yet_does_not_wait_either(monkeypatch: pytest.MonkeyPatch) -> None:
    voice_decision.reset_flag_cache()  # cold cache
    ws = _FakeSocket()
    state = _state(voice_decision_text="kal 5 baje call karna")
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(object(), delay=5.0)
    started = time.monotonic()
    await adapter._fire_pending_reply(ws, state)
    assert time.monotonic() - started < 0.3
    assert ws.sent == [{"type": "response.create"}]


async def test_no_task_is_even_started_for_an_org_known_not_to_be_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def fake_compute(*args: Any) -> None:
        started.append(args[3])

    monkeypatch.setattr(voice_decision, "compute_voice_plan", fake_compute)
    voice_decision.set_cached_voice_mode(ORG_ID, "shadow")
    state = _state()
    adapter._start_voice_decision(state, "hello")
    assert state.voice_decision_task is None and started == []

    # Unknown or expired -> a background check refreshes the cache, so switching
    # an org to live still takes effect.
    voice_decision.reset_flag_cache()
    adapter._start_voice_decision(state, "hello")
    assert state.voice_decision_task is not None
    await asyncio.sleep(0)
    assert started == ["hello"]


async def test_shadow_logging_does_not_open_a_session_for_non_shadow_orgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.core.conversation import shadow

    def boom() -> Any:
        raise AssertionError("a database session was opened for an org that is not in log-only mode")

    monkeypatch.setattr(shadow, "AsyncSessionLocal", boom)
    for mode in ("none", "off", "live"):
        voice_decision.set_cached_voice_mode(ORG_ID, mode)
        await shadow._run_shadow(ORG_ID, USER_ID, uuid.uuid4(), "hello there")  # must return quietly


def test_smart_mode_holds_for_a_stutter_but_not_a_plain_sentence() -> None:
    from apps.api.channels.voice.turn_controller import HOLD_CONTINUATION_SECONDS, hold_decision

    for text in ("mera bud-", "mera mera budget budget", "mujhe mujhe", "main chahta- "):
        assert hold_decision(text, False, smart=True) == (HOLD_CONTINUATION_SECONDS, "stutter"), text
        assert hold_decision(text, False, smart=False)[1] != "stutter"
    assert hold_decision("price kya hai", False, smart=True)[1] != "stutter"
    assert hold_decision("haan haan", True, smart=True)[1] != "stutter"
