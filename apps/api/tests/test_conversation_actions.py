"""Quotation 2.1: the backend runs approved actions through the existing
tools (callback, appointment, WhatsApp send, transfer to human). These tests
cover the two actions added on top of callback / transfer / not-interested:
book_appointment and send_whatsapp_message."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.engine import run_engine
from apps.api.core.conversation.understand import classify_with_rules
from apps.api.core.llm import ChatResult
from apps.api.db.models import Conversation, Org, User
from apps.api.db.models.conversation_engine import ConversationTurn, KnowledgeItem

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
TODAY = date(2026, 10, 15)


async def _no_llm(messages: list[dict[str, Any]]) -> ChatResult:
    raise AssertionError("LLM must not be called for a clear rules case")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return {"status": "ok"}


async def _seed(db: AsyncSession) -> Conversation:
    db.add(Org(id=ORG_ID, name="Test Org", conversation_mode="human_conversation"))
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
    conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp")
    db.add(conversation)
    await db.commit()
    return conversation


async def _run(
    db: AsyncSession, conversation: Conversation, text: str, *, channel: str = "whatsapp", **kw: Any
):
    kw.setdefault("llm", _no_llm)
    kw.setdefault("dispatch", _Recorder())
    return await run_engine(
        db,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation.id,
        channel=channel,
        text=text,
        history=[],
        api_key=None,
        **kw,
    )


async def _states(db: AsyncSession) -> list[str | None]:
    rows = (await db.execute(select(ConversationTurn).order_by(ConversationTurn.created_at))).scalars()
    return [row.state for row in rows]


# --- rules ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I want to book a demo tomorrow at 5 pm",
        "demo book karna hai kal 5 baje",
        "please schedule an appointment for tomorrow 11 am",
        "mujhe meeting fix karni hai kal shaam 6 baje",
    ],
)
def test_rules_detect_appointment_request(text: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY)
    assert result is not None
    assert result.scenario == "appointment_request"


@pytest.mark.parametrize(
    "text",
    [
        "send me the details on whatsapp",
        "whatsapp pe bhej do",
        "please share the price list on WhatsApp",
    ],
)
def test_rules_detect_whatsapp_info_request(text: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY)
    assert result is not None
    assert result.scenario == "whatsapp_info_request"


@pytest.mark.parametrize(
    ("text", "scenario"),
    [
        ("stop sending me messages on whatsapp", "not_interested"),
        ("busy, call me tomorrow at 5 pm", "busy_callback"),  # "call" alone is a callback
        ("schedule a call for tomorrow", None),  # ambiguous: left to the model
    ],
)
def test_new_rules_do_not_steal_existing_scenarios(text: str, scenario: str | None) -> None:
    result = classify_with_rules(text, state=None, today=TODAY)
    if scenario is None:
        assert result is None or result.scenario not in ("appointment_request", "whatsapp_info_request")
    else:
        assert result is not None and result.scenario == scenario


def test_awaiting_appointment_time_turns_a_time_into_an_appointment() -> None:
    result = classify_with_rules("kal 5 baje", state="awaiting_appointment_time", today=TODAY)
    assert result is not None
    assert result.scenario == "appointment_request"
    assert result.facts["callback_time_hhmm"] == "17:00"


# --- book_appointment --------------------------------------------------------------


async def test_appointment_with_date_and_time_runs_book_appointment(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session, conversation, "I want to book a demo tomorrow at 5 pm", dispatch=dispatch
    )
    assert result.reply is not None and result.fallback_reason is None
    (call,) = dispatch.calls
    assert call[0] == "book_appointment"
    assert call[1]["time"] == "17:00"
    assert call[1]["date"]
    assert "17:00" in result.reply
    assert (await _states(db_session)) == ["appointment_set"]


async def test_appointment_without_time_asks_then_books(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    first = await _run(db_session, conversation, "I want to book a demo", dispatch=dispatch)
    assert first.reply is not None and dispatch.calls == []
    second = await _run(db_session, conversation, "tomorrow 4 pm", dispatch=dispatch)
    assert second.reply is not None
    assert [c[0] for c in dispatch.calls] == ["book_appointment"]
    assert (await _states(db_session)) == ["awaiting_appointment_time", "appointment_set"]


async def test_appointment_tool_failure_falls_back(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)

    async def failing(action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "error", "reason": "slot_conflict"}

    result = await _run(
        db_session, conversation, "book a demo tomorrow at 5 pm", dispatch=failing
    )
    assert result.reply is None and result.fallback_reason == "action_failed"


# --- send_whatsapp_message ----------------------------------------------------------


async def _add_pricing(db: AsyncSession) -> None:
    db.add(
        KnowledgeItem(
            org_id=ORG_ID,
            kind="pricing",
            title="Premium plan price",
            body="The Premium plan costs Rs 1,00,000.",
            keywords="premium price plan details",
        )
    )
    await db.commit()


async def test_voice_whatsapp_request_sends_only_approved_text(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await _add_pricing(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session,
        conversation,
        "please send the premium plan details on whatsapp",
        channel="voice",
        dispatch=dispatch,
    )
    assert result.reply is not None
    (call,) = dispatch.calls
    assert call[0] == "send_whatsapp_message"
    assert "The Premium plan costs Rs 1,00,000." in call[1]["message"]
    assert (await _states(db_session)) == ["info_sent"]


async def test_whatsapp_request_without_knowledge_sends_nothing_invented(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session, conversation, "send me the details on whatsapp", channel="voice", dispatch=dispatch
    )
    assert result.reply is not None
    assert [c[0] for c in dispatch.calls] == ["transfer_to_human"]  # no message was sent


async def test_whatsapp_request_on_whatsapp_answers_in_chat(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await _add_pricing(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session,
        conversation,
        "send the premium plan details on whatsapp",
        channel="whatsapp",
        dispatch=dispatch,
    )
    # Already in WhatsApp: no second message is sent through the tool.
    assert dispatch.calls == []
    assert result.reply is not None and "1,00,000" in result.reply


async def test_shadow_mode_never_executes_the_new_actions(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    dispatch = _Recorder()
    result = await _run(
        db_session,
        conversation,
        "book a demo tomorrow at 5 pm",
        channel="voice",
        dispatch=dispatch,
        mode="shadow",
    )
    assert result.reply is not None
    assert dispatch.calls == []
    (turn,) = (await db_session.execute(select(ConversationTurn))).scalars().all()
    assert turn.action_id == "book_appointment" and turn.mode == "shadow"


# --- regressions found by the real-model voice check ----------------------------------------


@pytest.mark.parametrize(
    ("text", "state", "scenario"),
    [
        # A time inside a clear demo request is NOT the callback we were waiting on.
        ("I want to book a demo tomorrow at 4 pm", "awaiting_callback_time", "appointment_request"),
        # ... nor inside a request for details on WhatsApp or a price question.
        ("send the details on whatsapp at 5 pm", "awaiting_callback_time", "whatsapp_info_request"),
        ("premium plan ka price kya hai, kal 5 baje batana", "awaiting_callback_time", "price_question"),
        # An unremarkable answer to "when should we call?" still counts as the callback time.
        ("kal 5 baje", "awaiting_callback_time", "busy_callback"),
        ("ok tomorrow at 4 pm please", "awaiting_callback_time", "busy_callback"),
        # And the same for an appointment we asked a time for.
        ("tomorrow 4 pm", "awaiting_appointment_time", "appointment_request"),
        ("this is useless, tomorrow 4 pm", "awaiting_appointment_time", "angry"),
    ],
)
def test_waiting_state_only_claims_unremarkable_answers(text: str, state: str, scenario: str) -> None:
    result = classify_with_rules(text, state=state, today=TODAY)
    assert result is not None
    assert result.scenario == scenario


@pytest.mark.parametrize(
    "text",
    [
        "Kal shaam 5 bje call krna",
        "kal call kro 5 baje",
        "shaam ko call karo",
        "baad me call kr do",
    ],
)
def test_spelling_variants_of_call_later_are_recognised(text: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY)
    assert result is not None and result.scenario == "busy_callback"
