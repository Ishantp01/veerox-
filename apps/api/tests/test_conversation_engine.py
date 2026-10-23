"""Tests for the Conversation Intelligence Engine (core/conversation/).

LLM calls are injected (``llm=``) or patched at the ``chat_completion`` seam;
actions go through an injected ``dispatch`` recorder, so nothing touches the
network or the real tools except the AgentCore integration tests, which use
the real dispatch table against the in-memory DB.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core import agent as agent_module
from apps.api.core.agent import agent_core
from apps.api.core.conversation import engine as engine_module
from apps.api.core.conversation.decide import decide, validate_action
from apps.api.core.conversation.engine import run_engine
from apps.api.core.conversation.knowledge import find_knowledge
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.understand import (
    UnderstandError,
    classify_with_rules,
    detect_language,
    understand,
)
from apps.api.core.llm import ChatResult
from apps.api.db.models import Conversation, Lead, Org, User
from apps.api.db.models.conversation_engine import (
    ConversationMemory,
    ConversationTurn,
    KnowledgeItem,
)
from apps.api.deps import get_db, get_redis_dep
from apps.api.tests.conftest import FakeRedis

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
TODAY = date(2026, 10, 15)
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest.fixture(autouse=True)
def _pin_default_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_ID))


async def _seed(db: AsyncSession, *, mode: str = "human_conversation") -> Conversation:
    db.add(Org(id=ORG_ID, name="Test Org", conversation_mode=mode))
    db.add(Org(id=OTHER_ORG_ID, name="Other Org"))
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
    conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp")
    db.add(conversation)
    await db.commit()
    return conversation


def _llm_returning(content: str | None, calls: list[Any] | None = None):
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        if calls is not None:
            calls.append(messages)
        return ChatResult(content=content, tokens_in=10, tokens_out=5)

    return call


def _llm_json(**fields: Any):
    return _llm_returning(json.dumps(fields))


async def _no_llm(messages: list[dict[str, Any]]) -> ChatResult:
    raise AssertionError("LLM must not be called for a clear rules case")


class _Recorder:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result or {"status": "ok"}

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return self.result


async def _run(db: AsyncSession, conversation: Conversation, text: str, **kwargs: Any):
    kwargs.setdefault("llm", _no_llm)
    kwargs.setdefault("dispatch", _Recorder())
    return await run_engine(
        db,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation.id,
        channel="whatsapp",
        text=text,
        history=[],
        api_key=None,
        **kwargs,
    )


async def _turns(db: AsyncSession) -> list[ConversationTurn]:
    result = await db.execute(select(ConversationTurn).order_by(ConversationTurn.created_at))
    return list(result.scalars().all())


_CALLBACK_LLM = _llm_json(
    scenario="callback_request", intent="callback_request", emotion="neutral",
    language="en", confidence=0.95, facts={},
)


@pytest.mark.parametrize("text", ["Call me", "call karo", "mujhe call karo"])
async def test_plain_call_me_places_an_ai_call_now(db_session: AsyncSession, text: str) -> None:
    conversation = await _seed(db_session)
    recorder = _Recorder()

    result = await _run(db_session, conversation, text, dispatch=recorder, llm=_CALLBACK_LLM)

    assert recorder.calls == [("initiate_ai_call", {})]
    assert result.reply is not None and "call" in result.reply.lower()


@pytest.mark.parametrize("text", ["call me tomorrow", "call me at 6", "I am busy, call me later"])
async def test_call_me_with_a_time_or_busy_is_not_an_immediate_call(
    db_session: AsyncSession, text: str
) -> None:
    conversation = await _seed(db_session)
    recorder = _Recorder()

    await _run(db_session, conversation, text, dispatch=recorder)

    assert all(action != "initiate_ai_call" for action, _ in recorder.calls)


async def test_failed_call_falls_back_instead_of_claiming_a_call(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    recorder = _Recorder({"status": "error", "reason": "calling_not_configured"})

    result = await _run(db_session, conversation, "Call me", dispatch=recorder, llm=_CALLBACK_LLM)

    assert result.reply is None and result.fallback_reason == "action_failed"


async def test_saying_interested_qualifies_the_lead(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    lead = Lead(org_id=ORG_ID, user_id=USER_ID, name="Test", phone="+910000000001", status="new")
    done = Lead(org_id=ORG_ID, user_id=uuid.uuid4(), name="X", phone="+910000000002", status="lost")
    db_session.add_all([lead, done])
    await db_session.commit()

    await _run(db_session, conversation, "I am interested")

    await db_session.refresh(lead)
    assert lead.status == "qualified"


async def test_interested_never_reopens_a_lost_lead(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    lead = Lead(org_id=ORG_ID, user_id=USER_ID, name="Test", phone="+910000000001", status="lost")
    db_session.add(lead)
    await db_session.commit()

    await _run(db_session, conversation, "I am interested")

    await db_session.refresh(lead)
    assert lead.status == "lost"


# --- 2.0 understand: rules -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "scenario"),
    [
        ("I am busy, call me tomorrow", "busy_callback"),
        ("abhi busy hoon, kal call karna", "busy_callback"),
        ("अभी व्यस्त हूँ, बाद में बात करते हैं", "busy_callback"),
        ("How much does it cost?", "price_question"),
        ("iska price kitna hai", "price_question"),
        ("कीमत कितनी है", "price_question"),
        ("That is too expensive", "objection"),
        ("bahut mehenga hai", "objection"),
        ("Yes I am interested, send details", "interested"),
        ("haan batao", "interested"),
        ("not interested", "not_interested"),
        ("please stop messaging me", "not_interested"),
        ("mujhe nahi chahiye", "not_interested"),
        ("I don't understand what you mean", "confused"),
        ("samajh nahi aaya", "confused"),
        ("This is useless, worst service", "angry"),
        ("bakwas service hai", "angry"),
        ("I want to talk to a human", "human_handoff"),
        ("manager se baat karao", "human_handoff"),
        ("hello", "greeting"),
        ("नमस्ते", "greeting"),
    ],
)
def test_rules_classify_priority_scenarios(text: str, scenario: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY)
    assert result is not None, text
    assert result.scenario == scenario
    assert result.source == "rules"
    assert result.confidence >= 0.6


def test_rules_return_none_for_unclear_text() -> None:
    assert classify_with_rules("do you ship to Pune warehouse", state=None, today=TODAY) is None


def test_rules_defer_when_two_scenarios_tie() -> None:
    # price_question (0.8) vs objection (0.85) are within the clear margin only
    # when weights are close; "expensive ... how much" should not be silently
    # decided as one of them with high confidence — it must go to the LLM or
    # resolve to the stronger signal. Either way it must not be a hard-stop.
    result = classify_with_rules("it is expensive, how much discount", state=None, today=TODAY)
    assert result is None or result.scenario in ("objection", "price_question")


def test_hard_stop_beats_other_matches() -> None:
    result = classify_with_rules("this is useless, I want a human", state=None, today=TODAY)
    assert result is not None
    assert result.scenario in ("human_handoff", "angry")


def test_language_detection() -> None:
    assert detect_language("नमस्ते आप कैसे हैं") == "hi"
    assert detect_language("mujhe price batao kya hai") == "hinglish"
    assert detect_language("what is the price") == "en"


def test_callback_facts_extracted() -> None:
    result = classify_with_rules("busy, call me tomorrow at 5 pm", state=None, today=TODAY)
    assert result is not None
    assert result.facts["callback_date"] == "2026-10-16"
    assert result.facts["callback_time_hhmm"] == "17:00"


def test_bare_number_is_not_a_time() -> None:
    result = classify_with_rules("busy, I have 2 meetings", state=None, today=TODAY)
    assert result is not None
    assert "callback_time_hhmm" not in result.facts


def test_name_fact_extracted_but_not_stopwords() -> None:
    named = classify_with_rules("my name is Asha, how much is it", state=None, today=TODAY)
    assert named is not None and named.facts["name"] == "Asha"
    not_named = classify_with_rules("I am busy right now", state=None, today=TODAY)
    assert not_named is not None and "name" not in not_named.facts


def test_awaiting_callback_time_state_turns_time_into_callback() -> None:
    result = classify_with_rules("kal 5 baje", state="awaiting_callback_time", today=TODAY)
    assert result is not None
    assert result.scenario == "busy_callback"
    assert result.facts["callback_time_hhmm"] == "17:00"


# --- 2.0 understand: LLM path ----------------------------------------------


async def _understand_llm(llm, text: str = "do you ship to Pune") -> Any:
    return await understand(
        text, state=None, history=[], memory={}, llm=llm, timeout=1.0, today=TODAY
    )


async def test_llm_decides_ambiguous_turn() -> None:
    llm = _llm_json(
        scenario="unknown_question",
        intent="shipping",
        emotion="neutral",
        language="en",
        confidence=0.82,
        facts={"business_type": "retail"},
    )
    result = await _understand_llm(llm)
    assert result.source == "llm"
    assert result.scenario == "unknown_question"
    assert result.confidence == pytest.approx(0.82)
    assert result.facts["business_type"] == "retail"
    assert result.tokens_in == 10


async def test_llm_accepts_fenced_json() -> None:
    llm = _llm_returning(
        '```json\n{"scenario":"interested","intent":"x","emotion":"positive","language":"en","confidence":0.7}\n```'
    )
    assert (await _understand_llm(llm)).scenario == "interested"


async def test_llm_other_scenario_is_capped_below_threshold() -> None:
    llm = _llm_json(scenario="other", intent="x", emotion="neutral", language="en", confidence=0.99)
    result = await _understand_llm(llm)
    assert result.confidence <= 0.5


@pytest.mark.parametrize(
    "content",
    [None, "", "not json at all", '["list"]', '{"scenario":"bogus","confidence":0.9}',
     '{"scenario":"angry","confidence":"high"}'],
)
async def test_llm_invalid_output_raises(content: str | None) -> None:
    with pytest.raises(UnderstandError) as exc:
        await _understand_llm(_llm_returning(content))
    assert exc.value.reason == "invalid_output"


async def test_llm_timeout_raises() -> None:
    async def slow(messages: list[dict[str, Any]]) -> ChatResult:
        await asyncio.sleep(5)
        return ChatResult(content="{}")

    with pytest.raises(UnderstandError) as exc:
        await understand("do you ship", state=None, history=[], memory={}, llm=slow, timeout=0.05)
    assert exc.value.reason == "timeout"


async def test_llm_provider_error_raises() -> None:
    async def boom(messages: list[dict[str, Any]]) -> ChatResult:
        raise RuntimeError("provider down")

    with pytest.raises(UnderstandError) as exc:
        await understand("do you ship", state=None, history=[], memory={}, llm=boom, timeout=1.0)
    assert exc.value.reason == "llm_error"


# --- 4.0 decide / allow-list -----------------------------------------------


def test_validate_action_allow_list() -> None:
    assert validate_action("none", {})
    assert validate_action("transfer_to_human", {"reason": "x"})
    assert not validate_action("transfer_to_human", {})  # missing required arg
    assert not validate_action("request_callback", {"date": "2026-10-16"})  # missing time
    assert validate_action("book_appointment", {"date": "2026-10-16", "time": "17:00"})
    assert not validate_action("book_appointment", {"date": "2026-10-16"})  # missing time
    assert validate_action("send_whatsapp_message", {"message": "hello"})
    assert not validate_action("send_whatsapp_message", {})  # missing message
    assert not validate_action("send_whatsapp_file", {"x": 1})  # real tool, not allow-listed
    assert not validate_action("initiate_ai_call", {})
    assert not validate_action("drop_all_tables", {})


def test_decide_unknown_question_never_guesses() -> None:
    understanding = classify_with_rules("hello", state=None, today=TODAY)
    assert understanding is not None
    understanding.scenario = "unknown_question"
    decision = decide(understanding, text="do you ship to Pune", memory={}, knowledge=[])
    assert decision.action_id == "transfer_to_human"
    assert decision.reply_kind == "unknown_followup"
    assert decision.args["urgency"] == "low"


def test_decide_callback_without_time_asks_for_it() -> None:
    understanding = classify_with_rules("I am busy right now", state=None, today=TODAY)
    assert understanding is not None
    decision = decide(understanding, text="busy", memory={}, knowledge=[])
    assert decision.action_id == "none"
    assert decision.reply_kind == "ask_callback_time"
    assert decision.next_state == "awaiting_callback_time"


# --- 3.0 memory --------------------------------------------------------------


async def test_memory_roundtrip_and_whitelist(db_session: AsyncSession) -> None:
    await _seed(db_session)
    written = await save_facts(
        db_session, ORG_ID, USER_ID, {"name": "Asha", "password": "hunter2", "business_type": " "}
    )
    await db_session.commit()
    assert written == ["name"]
    assert await load_memory(db_session, ORG_ID, USER_ID) == {"name": "Asha"}


async def test_memory_expiry_rules(db_session: AsyncSession) -> None:
    await _seed(db_session)
    await save_facts(
        db_session, ORG_ID, USER_ID, {"name": "Asha", "callback_time": "2026-10-16 17:00"}
    )
    await db_session.commit()

    rows = {
        r.key: r
        for r in (await db_session.execute(select(ConversationMemory))).scalars().all()
    }
    now = datetime.now(UTC)
    assert rows["callback_time"].scope == "call"
    assert rows["name"].scope == "persistent"
    call_ttl = rows["callback_time"].expires_at.replace(tzinfo=UTC) - now
    assert timedelta(minutes=29) < call_ttl <= timedelta(minutes=30)
    assert rows["name"].expires_at.replace(tzinfo=UTC) - now > timedelta(days=89)

    # Expire the call-scoped fact: it disappears, the persistent one stays.
    rows["callback_time"].expires_at = now - timedelta(seconds=1)
    await db_session.commit()
    assert await load_memory(db_session, ORG_ID, USER_ID) == {"name": "Asha"}


async def test_memory_rewrite_refreshes_expiry(db_session: AsyncSession) -> None:
    await _seed(db_session)
    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha"})
    row = (await db_session.execute(select(ConversationMemory))).scalar_one()
    row.expires_at = datetime.now(UTC) + timedelta(days=1)
    await db_session.commit()

    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha K"})
    await db_session.commit()
    row = (await db_session.execute(select(ConversationMemory))).scalar_one()
    assert row.value == "Asha K"
    assert row.expires_at.replace(tzinfo=UTC) - datetime.now(UTC) > timedelta(days=89)


# --- D4 knowledge --------------------------------------------------------------


async def test_knowledge_match_scoping_and_inactive(db_session: AsyncSession) -> None:
    await _seed(db_session)
    db_session.add_all(
        [
            KnowledgeItem(org_id=ORG_ID, kind="pricing", title="Starter plan price",
                          body="Starter is Rs 999 per month.", keywords="price cost plan starter"),
            KnowledgeItem(org_id=ORG_ID, kind="faq", title="Retired plan price",
                          body="old", keywords="price", active=False),
            KnowledgeItem(org_id=OTHER_ORG_ID, kind="pricing", title="Other org price",
                          body="Other org secret price.", keywords="price"),
        ]
    )
    await db_session.commit()

    found = await find_knowledge(db_session, ORG_ID, "what is the price of the starter plan", "en")
    assert [item.title for item in found] == ["Starter plan price"]
    assert await find_knowledge(db_session, ORG_ID, "do you ship to Pune", "en") == []
    assert await find_knowledge(db_session, ORG_ID, "", "en") == []


async def test_knowledge_language_filter(db_session: AsyncSession) -> None:
    await _seed(db_session)
    db_session.add(
        KnowledgeItem(org_id=ORG_ID, kind="faq", title="Timing", body="9 se 6", keywords="timing",
                      language="hi")
    )
    await db_session.commit()
    assert await find_knowledge(db_session, ORG_ID, "timing", "en") == []
    assert len(await find_knowledge(db_session, ORG_ID, "timing", "hinglish")) == 1


# --- engine end to end -----------------------------------------------------------


async def test_engine_callback_runs_approved_action_and_logs(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session, conversation, "busy hoon, kal 5 baje call karna", dispatch=dispatch
    )

    assert result.reply is not None and result.fallback_reason is None
    assert dispatch.calls[0][0] == "request_callback"
    assert dispatch.calls[0][1]["time"] == "17:00"

    (turn,) = await _turns(db_session)
    assert turn.mode == "live"
    assert turn.scenario == "busy_callback"
    assert turn.action_id == "request_callback"
    assert turn.reply_text == result.reply
    assert turn.fallback_reason is None
    assert turn.state == "callback_set"
    assert turn.source == "rules"
    assert turn.latency_ms is not None


async def test_engine_state_carries_across_turns(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    first = await _run(db_session, conversation, "I am busy right now", dispatch=dispatch)
    assert first.reply is not None and dispatch.calls == []

    second = await _run(db_session, conversation, "kal 5 baje", dispatch=dispatch)
    assert second.reply is not None
    assert dispatch.calls[0][0] == "request_callback"
    assert [t.state for t in await _turns(db_session)] == ["awaiting_callback_time", "callback_set"]


async def test_engine_not_interested_and_handoff_actions(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    await _run(db_session, conversation, "not interested", dispatch=dispatch)
    await _run(db_session, conversation, "I want to talk to a human", dispatch=dispatch)
    await _run(db_session, conversation, "this is useless", dispatch=dispatch)
    assert [c[0] for c in dispatch.calls] == [
        "mark_not_interested",
        "transfer_to_human",
        "transfer_to_human",
    ]
    assert dispatch.calls[2][1]["urgency"] == "high"


async def test_engine_answers_only_from_approved_knowledge(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    db_session.add(
        KnowledgeItem(org_id=ORG_ID, kind="pricing", title="Starter plan price",
                      body="Starter is Rs 999 per month.", keywords="price cost plan starter")
    )
    await db_session.commit()

    prompts: list[Any] = []
    llm = _llm_returning("Starter plan Rs 999 per month hai.", calls=prompts)
    result = await _run(db_session, conversation, "what is the price of starter plan", llm=llm)

    assert result.reply == "Starter plan Rs 999 per month hai."
    assert "Starter is Rs 999 per month." in prompts[0][0]["content"]
    (turn,) = await _turns(db_session)
    assert turn.scenario == "price_question"
    assert len(turn.knowledge_ids) == 1


async def test_engine_knowledge_phrasing_failure_uses_approved_text(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    db_session.add(
        KnowledgeItem(org_id=ORG_ID, kind="pricing", title="Starter plan price",
                      body="Starter is Rs 999 per month.", keywords="price starter")
    )
    await db_session.commit()

    async def boom(messages: list[dict[str, Any]]) -> ChatResult:
        raise RuntimeError("down")

    result = await _run(db_session, conversation, "starter plan price?", llm=boom)
    assert result.reply == "Starter is Rs 999 per month."


async def test_engine_unknown_question_hands_off_instead_of_guessing(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    llm = _llm_json(scenario="unknown_question", intent="shipping", emotion="neutral",
                    language="en", confidence=0.8)
    result = await _run(db_session, conversation, "do you deliver to Pune", llm=llm, dispatch=dispatch)

    assert result.reply is not None
    assert dispatch.calls[0][0] == "transfer_to_human"
    assert dispatch.calls[0][1]["urgency"] == "low"
    (turn,) = await _turns(db_session)
    assert turn.source == "llm" and turn.fallback_reason is None


@pytest.mark.parametrize(
    ("llm", "reason"),
    [
        (_llm_returning("garbage"), "invalid_output"),
        (
            _llm_json(scenario="interested", intent="x", emotion="neutral", language="en",
                      confidence=0.2),
            "low_confidence",
        ),
        (
            _llm_json(scenario="interested", intent="x", emotion="neutral", language="en",
                      confidence=0.9, action_id="drop_all_tables"),
            "invalid_action",
        ),
    ],
)
async def test_engine_fallbacks_return_no_reply_and_log(
    db_session: AsyncSession, llm: Any, reason: str
) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(db_session, conversation, "do you deliver to Pune", llm=llm, dispatch=dispatch)

    assert result.reply is None
    assert result.fallback_reason == reason
    assert dispatch.calls == []
    (turn,) = await _turns(db_session)
    assert turn.fallback_reason == reason
    assert turn.reply_text is None


async def test_engine_llm_timeout_falls_back(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await _seed(db_session)
    monkeypatch.setattr(settings, "conversation_llm_timeout_seconds", 0.05)

    async def slow(messages: list[dict[str, Any]]) -> ChatResult:
        await asyncio.sleep(5)
        return ChatResult(content="{}")

    result = await _run(db_session, conversation, "do you deliver to Pune", llm=slow)
    assert result.reply is None and result.fallback_reason == "timeout"


async def test_engine_action_failure_falls_back(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder({"status": "error", "reason": "boom"})
    result = await _run(db_session, conversation, "not interested", dispatch=dispatch)
    assert result.reply is None and result.fallback_reason == "action_failed"


async def test_engine_internal_error_falls_back_instead_of_raising(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conversation = await _seed(db_session)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    monkeypatch.setattr(engine_module, "decide", explode)
    result = await _run(db_session, conversation, "not interested")
    assert result.reply is None and result.fallback_reason == "error"


async def test_engine_saves_memory_facts(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await _run(db_session, conversation, "my name is Asha, I am busy, call tomorrow at 5 pm")
    memory = await load_memory(db_session, ORG_ID, USER_ID)
    assert memory["name"] == "Asha"
    assert memory["callback_time"] == "2026-10-16 17:00" or memory["callback_time"].endswith("17:00")


async def test_shadow_mode_logs_but_never_acts(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session, conversation, "my name is Asha, not interested", dispatch=dispatch, mode="shadow"
    )
    assert result.reply is not None
    assert dispatch.calls == []  # no action executed
    assert await load_memory(db_session, ORG_ID, USER_ID) == {}  # no memory written
    (turn,) = await _turns(db_session)
    assert turn.mode == "shadow" and turn.action_id == "mark_not_interested"


async def test_shadow_does_not_call_phrasing_llm(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    db_session.add(
        KnowledgeItem(org_id=ORG_ID, kind="pricing", title="Starter plan price",
                      body="Starter is Rs 999 per month.", keywords="price starter")
    )
    await db_session.commit()
    result = await _run(db_session, conversation, "starter plan price?", mode="shadow")
    assert result.reply == "Starter is Rs 999 per month."  # _no_llm would have raised


# --- AgentCore integration -------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


@pytest.fixture
def agent_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(agent_module, "get_redis_pool", lambda: fake)
    return fake


def _patch_legacy_llm(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    async def fake(messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        calls.append(messages)
        return ChatResult(content="legacy reply", tokens_in=3, tokens_out=2)

    monkeypatch.setattr(agent_module, "chat_completion", fake)
    return calls


async def test_handle_turn_strict_mode_never_uses_engine(
    db_session: AsyncSession, agent_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session, mode="strict_script")
    legacy_calls = _patch_legacy_llm(monkeypatch)

    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("engine ran for a strict_script org")

    monkeypatch.setattr(agent_module, "run_engine", explode)
    reply = await agent_core.handle_turn(
        db=db_session, user_id=USER_ID, channel="whatsapp", input_text="not interested"
    )
    assert reply == "legacy reply"
    assert len(legacy_calls) == 1
    assert await _turns(db_session) == []


async def test_handle_turn_human_mode_uses_engine_and_real_tool(
    db_session: AsyncSession, agent_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    legacy_calls = _patch_legacy_llm(monkeypatch)

    reply = await agent_core.handle_turn(
        db=db_session, user_id=USER_ID, channel="whatsapp", input_text="please stop messaging me"
    )
    assert reply != "legacy reply"
    assert legacy_calls == []  # the script path never ran
    (turn,) = await _turns(db_session)
    assert turn.action_id == "mark_not_interested"
    assert turn.action_result is not None and turn.action_result.get("status") != "error"


async def test_handle_turn_human_mode_falls_back_to_legacy(
    db_session: AsyncSession, agent_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    legacy_calls = _patch_legacy_llm(monkeypatch)

    async def garbage(messages: list[dict[str, Any]], **kwargs: Any) -> ChatResult:
        return ChatResult(content="not json", tokens_in=7, tokens_out=1)

    monkeypatch.setattr(engine_module, "chat_completion", garbage)
    reply = await agent_core.handle_turn(
        db=db_session, user_id=USER_ID, channel="whatsapp", input_text="do you deliver to Pune"
    )
    assert reply == "legacy reply"
    assert len(legacy_calls) == 1
    (turn,) = await _turns(db_session)
    assert turn.fallback_reason == "invalid_output"


async def test_handle_turn_voice_channel_never_uses_engine(
    db_session: AsyncSession, agent_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _patch_legacy_llm(monkeypatch)

    async def explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("engine must not run live on voice")

    monkeypatch.setattr(agent_module, "run_engine", explode)
    reply = await agent_core.handle_turn(
        db=db_session, user_id=USER_ID, channel="voice", input_text="not interested"
    )
    assert reply == "legacy reply"


# --- voice shadow scheduling -------------------------------------------------------


async def test_schedule_voice_shadow_respects_switch_and_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.core.conversation import shadow

    ran: list[str] = []

    async def fake_run(org_id: Any, user_id: Any, conversation_id: Any, text: str) -> None:
        ran.append(text)

    monkeypatch.setattr(shadow, "_run_shadow", fake_run)
    ids = (ORG_ID, USER_ID, uuid.uuid4())

    monkeypatch.setattr(settings, "conversation_voice_shadow", False)
    shadow.schedule_voice_shadow(*ids, "hello there")
    monkeypatch.setattr(settings, "conversation_voice_shadow", True)
    shadow.schedule_voice_shadow(*ids, "(voice)")
    shadow.schedule_voice_shadow(*ids, "real transcript")
    await asyncio.sleep(0.05)
    assert ran == ["real transcript"]


async def test_shadow_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.core.conversation import shadow

    async def boom(*args: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(shadow, "_run_shadow", boom)
    await shadow._guarded(ORG_ID, USER_ID, uuid.uuid4(), "text")  # must not raise


# --- admin endpoints -----------------------------------------------------------------


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield FakeRedis()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_endpoints_require_auth(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    for path in ("/conversation-engine/status", "/conversation-engine/turns",
                 "/conversation-engine/knowledge"):
        assert (await client.get(path)).status_code in (401, 403)


async def test_status_endpoint(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    response = await client.get("/conversation-engine/status", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_mode"] == "human_conversation"
    assert body["active_config_version"] is None and body["has_draft"] is False


async def test_knowledge_crud_is_org_scoped(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    other = KnowledgeItem(org_id=OTHER_ORG_ID, kind="faq", title="Other", body="x")
    db_session.add(other)
    await db_session.commit()

    created = await client.post(
        "/conversation-engine/knowledge",
        headers=ADMIN_HEADERS,
        json={"kind": "pricing", "title": "Starter", "body": "Rs 999", "keywords": "price"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    listed = (await client.get("/conversation-engine/knowledge", headers=ADMIN_HEADERS)).json()
    assert [row["title"] for row in listed] == ["Starter"]  # other org's row not visible

    patched = await client.patch(
        f"/conversation-engine/knowledge/{item_id}", headers=ADMIN_HEADERS, json={"active": False}
    )
    assert patched.status_code == 200 and patched.json()["active"] is False

    # Cannot touch another org's item.
    assert (
        await client.patch(
            f"/conversation-engine/knowledge/{other.id}", headers=ADMIN_HEADERS, json={"title": "x"}
        )
    ).status_code == 404
    assert (
        await client.delete(f"/conversation-engine/knowledge/{other.id}", headers=ADMIN_HEADERS)
    ).status_code == 404

    assert (
        await client.delete(f"/conversation-engine/knowledge/{item_id}", headers=ADMIN_HEADERS)
    ).status_code == 204


async def test_knowledge_rejects_bad_kind(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    response = await client.post(
        "/conversation-engine/knowledge",
        headers=ADMIN_HEADERS,
        json={"kind": "secrets", "title": "x", "body": "y"},
    )
    assert response.status_code == 422


async def test_turn_log_endpoint_filters_and_scopes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    conversation = await _seed(db_session)
    await _run(db_session, conversation, "not interested")
    await _run(db_session, conversation, "do you deliver to Pune", llm=_llm_returning("garbage"))
    db_session.add(
        ConversationTurn(org_id=OTHER_ORG_ID, channel="whatsapp", mode="live", user_text="secret")
    )
    await db_session.commit()

    everything = (await client.get("/conversation-engine/turns", headers=ADMIN_HEADERS)).json()
    assert len(everything) == 2  # other org's turn is invisible
    assert {t["user_text"] for t in everything} == {"not interested", "do you deliver to Pune"}

    fallbacks = (
        await client.get("/conversation-engine/turns?fallbacks_only=true", headers=ADMIN_HEADERS)
    ).json()
    assert [t["fallback_reason"] for t in fallbacks] == ["invalid_output"]

    shadow_only = (
        await client.get("/conversation-engine/turns?mode=shadow", headers=ADMIN_HEADERS)
    ).json()
    assert shadow_only == []

    first = next(t for t in everything if t["user_text"] == "not interested")
    assert first["scenario"] == "not_interested" and first["action_id"] == "mark_not_interested"
    assert first["confidence"] is not None and first["reply_text"]


async def test_platform_admin_can_set_conversation_mode_and_rejects_bad_value(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, mode="strict_script")
    bad = await client.patch(
        f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_mode": "yolo"}
    )
    assert bad.status_code == 422

    ok = await client.patch(
        f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_mode": "human_conversation"}
    )
    assert ok.status_code == 200, ok.text
    await db_session.refresh(await db_session.get(Org, ORG_ID))
    assert (await db_session.get(Org, ORG_ID)).conversation_mode == "human_conversation"


async def test_new_orgs_default_to_strict_script(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Fresh"))
    await db_session.commit()
    assert (await db_session.get(Org, ORG_ID)).conversation_mode == "strict_script"


# --- regressions found by the live run -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Do you integrate with Tally?",  # "rate" inside "integrate"
        "I am already done with that",  # "ready" inside "already"
        "I would rather not say",  # "rate" prefix of "rather"
        "we are planning something big",  # "plan" prefix of "planning"
    ],
)
def test_rules_do_not_match_inside_other_words(text: str) -> None:
    assert classify_with_rules(text, state=None, today=TODAY) is None


async def test_objection_without_knowledge_offers_call_instead_of_handoff(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(db_session, conversation, "bahut mehenga hai", dispatch=dispatch)
    assert result.reply is not None
    assert dispatch.calls == []
    (turn,) = await _turns(db_session)
    assert turn.scenario == "objection" and turn.state == "awaiting_callback_time"


async def test_llm_path_reply_language_follows_current_message() -> None:
    # The model drifts toward earlier Hinglish turns; the reply language must
    # follow what the customer just wrote.
    llm = _llm_json(scenario="unknown_question", intent="x", emotion="neutral",
                    language="hinglish", confidence=0.8)
    result = await _understand_llm(llm, text="Do you integrate with Tally?")
    assert result.language == "en"


@pytest.mark.parametrize(
    ("text", "channel"),
    [
        ("busy, call me tomorrow", None),
        ("stop calling me", None),
        ("abhi busy hoon, kal call karna", None),
        ("please message me on whatsapp", "whatsapp"),
        ("email me the details", "email"),
        ("I prefer call", "call"),
    ],
)
def test_preferred_channel_only_from_explicit_mention(text: str, channel: str | None) -> None:
    from apps.api.core.conversation.understand import _extract_facts

    assert _extract_facts(text, TODAY).get("preferred_channel") == channel


# --- voice mode setting (Decision Layer) -----------------------------------------------


async def test_new_orgs_default_to_log_only_voice(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Fresh"))
    await db_session.commit()
    assert (await db_session.get(Org, ORG_ID)).conversation_voice_mode == "shadow"


async def test_platform_admin_sets_and_validates_the_voice_mode(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session, mode="strict_script")
    bad = await client.patch(
        f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_voice_mode": "loud"}
    )
    assert bad.status_code == 422

    for mode in ("live", "off", "shadow"):
        ok = await client.patch(
            f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_voice_mode": mode}
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["conversation_voice_mode"] == mode

    # Omitting the field leaves it alone (PATCH semantics).
    await client.patch(f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_voice_mode": "live"})
    other = await client.patch(
        f"/billing/orgs/{ORG_ID}", headers=ADMIN_HEADERS, json={"conversation_mode": "human_conversation"}
    )
    assert other.json()["conversation_voice_mode"] == "live"


async def test_shadow_run_skips_orgs_that_are_not_in_log_only_mode(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, test_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from apps.api.core.conversation import shadow

    conversation = await _seed(db_session)
    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(shadow, "AsyncSessionLocal", factory)
    from apps.api.core.conversation import voice_decision

    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", factory)  # never a real database
    ran: list[str] = []

    async def fake_run_engine(*args: Any, **kwargs: Any) -> None:
        ran.append(kwargs["mode"])

    monkeypatch.setattr(shadow, "run_engine", fake_run_engine)

    org = await db_session.get(Org, ORG_ID)
    for voice_mode, expected in (("shadow", ["shadow"]), ("live", []), ("off", [])):
        org.conversation_voice_mode = voice_mode
        await db_session.commit()
        voice_decision.reset_flag_cache()
        ran.clear()
        await shadow._run_shadow(ORG_ID, USER_ID, conversation.id, "hello there")
        assert ran == expected, voice_mode
