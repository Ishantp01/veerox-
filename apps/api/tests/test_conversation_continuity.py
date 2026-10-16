"""Decision Layer 2.1: "maintain conversation continuity using existing
memory". Replies use what is already known about the customer and what was
already said, on WhatsApp and on live voice calls."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import adapter
from apps.api.core.conversation import voice_decision
from apps.api.core.conversation.engine import run_engine
from apps.api.core.conversation.memory import save_facts
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.voice_decision import VoicePlan, build_instruction
from apps.api.core.llm import ChatResult
from apps.api.db.models import Conversation, Org, User
from apps.api.db.models.conversation_engine import KnowledgeItem

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


async def _no_llm(messages: list[dict[str, Any]]) -> ChatResult:
    raise AssertionError("LLM must not be called for a clear rules case")


class _Recorder:
    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}


async def _seed(db: AsyncSession) -> Conversation:
    db.add(Org(id=ORG_ID, name="Test Org", conversation_mode="human_conversation"))
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
    conversation = Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp")
    db.add(conversation)
    await db.commit()
    return conversation


async def _run(
    db: AsyncSession,
    conversation: Conversation,
    text: str,
    *,
    history: list[dict[str, Any]] | None = None,
    llm: Any = _no_llm,
):
    return await run_engine(
        db,
        org_id=ORG_ID,
        user_id=USER_ID,
        conversation_id=conversation.id,
        channel="whatsapp",
        text=text,
        history=history or [],
        api_key=None,
        dispatch=_Recorder(),
        llm=llm,
    )


# --- WhatsApp replies ---------------------------------------------------------------


async def test_first_greeting_is_a_greeting(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    result = await _run(db_session, conversation, "hello")
    assert result.reply == "Hello! How can I help you today?"


async def test_greeting_uses_a_remembered_name(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha"})
    await db_session.commit()
    result = await _run(db_session, conversation, "hello")
    assert result.reply == "Hello Asha! How can I help you today?"


async def test_a_second_greeting_does_not_start_over(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hello! How can I help you today?"},
    ]
    result = await _run(db_session, conversation, "hello", history=history)
    assert result.reply == "I'm here. What would you like to know?"


async def test_hindi_and_hinglish_greetings_also_adapt(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha"})
    await db_session.commit()
    hindi = await _run(db_session, conversation, "नमस्ते")
    assert hindi.reply is not None and "Asha" in hindi.reply and hindi.reply.startswith("नमस्ते")
    again = await _run(
        db_session,
        conversation,
        "नमस्ते",
        history=[{"role": "assistant", "content": "x"}],
    )
    assert again.reply == "मैं यहीं हूँ। आप क्या जानना चाहेंगे?"


async def test_callback_confirmation_uses_the_name(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha"})
    await db_session.commit()
    result = await _run(db_session, conversation, "I am busy, call me tomorrow at 5 pm")
    assert result.reply is not None and "Thank you Asha!" in result.reply
    hinglish = await _run(db_session, conversation, "abhi busy hoon, kal 5 baje call karna")
    assert hinglish.reply is not None and "Dhanyavaad Asha!" in hinglish.reply


async def test_replies_without_a_name_are_unchanged(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    result = await _run(db_session, conversation, "I am busy, call me tomorrow at 5 pm")
    assert result.reply is not None and result.reply.endswith("Thank you!")


async def test_knowledge_phrasing_gets_the_conversation_and_known_facts(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed(db_session)
    db_session.add(
        KnowledgeItem(
            org_id=ORG_ID,
            kind="pricing",
            title="Premium plan price",
            body="The Premium plan costs Rs 1,00,000.",
            keywords="premium price plan",
        )
    )
    await db_session.commit()
    await save_facts(db_session, ORG_ID, USER_ID, {"name": "Asha", "business_type": "travel agency"})
    await db_session.commit()

    prompts: list[list[dict[str, Any]]] = []

    async def llm(messages: list[dict[str, Any]]) -> ChatResult:
        prompts.append(messages)
        return ChatResult(content="It is Rs 1 lakh.", tokens_in=1, tokens_out=1)

    history = [
        {"role": "user", "content": "we run a travel agency"},
        {"role": "assistant", "content": "Great, tell me what you need."},
    ]
    result = await _run(db_session, conversation, "premium plan price kya hai", history=history, llm=llm)

    assert result.reply == "It is Rs 1 lakh."
    system = prompts[0][0]["content"]
    assert "The Premium plan costs Rs 1,00,000." in system  # still only approved facts
    assert "name: Asha" in system and "business_type: travel agency" in system
    assert "we run a travel agency" in system and "Great, tell me what you need." in system
    assert "do not repeat what the customer was already told" in system


async def test_no_context_block_when_there_is_nothing_to_add(db_session: AsyncSession) -> None:
    conversation = await _seed(db_session)
    db_session.add(
        KnowledgeItem(
            org_id=ORG_ID, kind="pricing", title="Premium plan price",
            body="The Premium plan costs Rs 1,00,000.", keywords="premium price plan",
        )
    )
    await db_session.commit()
    prompts: list[list[dict[str, Any]]] = []

    async def llm(messages: list[dict[str, Any]]) -> ChatResult:
        prompts.append(messages)
        return ChatResult(content="ok", tokens_in=1, tokens_out=1)

    await _run(db_session, conversation, "premium plan price kya hai", llm=llm)
    assert "Recent conversation" not in prompts[0][0]["content"]
    assert "Known about the customer" not in prompts[0][0]["content"]


# --- live voice ------------------------------------------------------------------------


def _plan(memory: dict[str, str], facts: dict[str, str], kind: str = "ask_callback_time") -> VoicePlan:
    understanding = Understanding(
        scenario="busy_callback", intent="x", emotion="neutral", language="hinglish",
        confidence=0.9, source="rules", facts=facts,
    )
    decision = Decision("none", {}, kind)
    return VoicePlan(understanding, decision, memory, None, time.monotonic())


def test_voice_instruction_lists_what_is_already_known() -> None:
    text = build_instruction(
        _plan({"name": "Asha", "business_type": "travel agency"}, {"preferred_channel": "whatsapp"}),
        action_result=None,
    )
    assert "do not ask for it again" in text
    assert "name: Asha" in text and "business_type: travel agency" in text
    assert "preferred_channel: whatsapp" in text
    assert "Do not repeat information you have already given" in text


def test_voice_instruction_without_known_facts_only_adds_the_no_repeat_rule() -> None:
    text = build_instruction(_plan({}, {}), action_result=None)
    assert "Already known" not in text
    assert "Do not repeat information you have already given" in text


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _FakeLog:
    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


async def test_a_real_transcript_event_starts_a_decision_that_steers_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the adapter's real event handler: the caller's final transcript
    arrives, a decision is started from it, and the next reply is steered."""
    voice_decision.set_cached_voice_mode(ORG_ID, "live")
    seen: list[str] = []

    async def fake_compute(org_id: Any, user_id: Any, conversation_id: Any, text: str) -> VoicePlan:
        seen.append(text)
        return _plan({"name": "Asha"}, {}, kind="callback_confirmed")

    applied: list[Any] = []

    async def fake_apply(plan: Any, **kwargs: Any) -> voice_decision.VoiceReply:
        applied.append(kwargs["text"])
        return voice_decision.VoiceReply("DECISION FOR THIS TURN: confirm the callback", True)

    monkeypatch.setattr(voice_decision, "compute_voice_plan", fake_compute)
    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)

    ws = _FakeSocket()
    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE PROMPT"
    )
    await adapter.handle_openai_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "kal shaam 5 baje call karna",
        },
        None,  # call_ws: unused by this event
        ws,
        state,
        _FakeLog(),
    )
    assert state.voice_decision_task is not None
    assert state.voice_decision_text == "kal shaam 5 baje call karna"

    await adapter._fire_pending_reply(ws, state)
    assert seen == ["kal shaam 5 baje call karna"]
    assert applied == ["kal shaam 5 baje call karna"]
    (message,) = ws.sent
    assert message["response"]["instructions"].startswith("BASE PROMPT")
    assert message["response"]["tool_choice"] == "none"


async def test_filler_while_the_agent_is_talking_does_not_start_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def fake_compute(*args: Any) -> None:
        started.append(args[3])

    monkeypatch.setattr(voice_decision, "compute_voice_plan", fake_compute)
    ws = _FakeSocket()
    state = adapter.CallState(
        user_id=USER_ID, conversation_id=uuid.uuid4(), org_id=ORG_ID, base_instructions="BASE"
    )
    state.interrupted_mid_response = True  # the caller spoke over the agent
    await adapter.handle_openai_event(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "ok"},
        None,
        ws,
        state,
        _FakeLog(),
    )
    assert state.voice_decision_task is None and started == []
