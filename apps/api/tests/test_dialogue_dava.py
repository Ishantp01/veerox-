"""Phase 4 behavioural regression: a synthetic franchise-enquiry call, driven
through the real decision layer (voice) and engine (text) against the in-memory
database with mocked model calls.

The call is modelled on the franchise conversation analysed earlier, but every
name, number and business fact here is synthetic ("SPF Pharmacy Franchise").
Each test states which of the twelve required behaviours it protects.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.core.conversation import builder, dialogue, engine, runtime_stats, voice_decision
from apps.api.core.conversation.config import DEFAULT_SCENARIOS, reset_config_cache
from apps.api.core.conversation.engine import run_engine
from apps.api.core.conversation.memory import load_memory
from apps.api.core.conversation.voice_decision import VoiceReply, apply_voice_plan, compute_voice_plan
from apps.api.core.llm import ChatResult
from apps.api.db.models import Appointment, Conversation, Lead, Org, User
from apps.api.db.models.conversation_engine import ConversationTurn, KnowledgeItem

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000aaaa")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000bbbb")
TOMORROW = (date.today() + timedelta(days=1)).isoformat()

FACTS = [
    ("pricing", "Franchise investment", "investment nivesh lagat cost",
     "The franchise investment depends on the store format. The franchise team shares the exact figure after a first conversation."),
    ("faq", "First meeting", "meeting commitment obligation",
     "The first meeting is informational. It does not commit you to anything."),
    ("faq", "Margin structure", "margin profit",
     "Margins depend on the product mix. The team explains the margin structure in the first conversation."),
]


# --- fixtures ---------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_config_cache()
    voice_decision.reset_flag_cache()
    runtime_stats.reset()
    monkeypatch.setattr(settings, "conversation_voice_live", True)
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 1.0)
    monkeypatch.setattr(settings, "conversation_voice_llm_timeout_seconds", 0.9)
    monkeypatch.setattr(settings, "default_org_id", str(ORG_A))
    yield
    reset_config_cache()
    voice_decision.reset_flag_cache()
    runtime_stats.reset()


@pytest.fixture
def factory(test_engine, monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    fac = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(voice_decision, "AsyncSessionLocal", fac)
    return fac


def _llm(scenario: str = "discovery", confidence: float = 0.9, calls: list[str] | None = None):
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        if calls is not None:
            calls.append(messages[-1]["content"])
        return ChatResult(
            content=json.dumps({"scenario": scenario, "intent": "x", "emotion": "neutral",
                                "confidence": confidence, "facts": {}, "action_id": "none"}),
            tokens_in=1, tokens_out=1,
        )

    return call


class Tools:
    """Records every action the engine asks the existing tools to run."""

    def __init__(self, **results: dict[str, Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._results = results

    async def __call__(self, action_id: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((action_id, args))
        return self._results.get(action_id, {"status": "ok"})

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


async def _seed(
    fac: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
    enhanced: bool = True,
    facts: bool = True,
    channel: str = "voice",
) -> uuid.UUID:
    async with fac() as db:
        db.add(Org(id=org_id, name=f"Org {str(org_id)[-1]}", conversation_mode="human_conversation",
                   conversation_voice_mode="live"))
        db.add(User(id=user_id, org_id=org_id, phone=f"+9100000000{str(user_id)[-1]}", name=None))
        db.add(Lead(org_id=org_id, user_id=user_id, phone=f"+9100000000{str(user_id)[-1]}", channel=channel))
        conversation = Conversation(org_id=org_id, user_id=user_id, channel=channel)
        db.add(conversation)
        if facts:
            for kind, title, keywords, body in FACTS:
                db.add(KnowledgeItem(org_id=org_id, kind=kind, title=title, keywords=keywords, body=body))
        await db.commit()
        if enhanced:
            payload = {
                "scenarios": [s.to_json() for s in DEFAULT_SCENARIOS],
                "settings": {"low_confidence_action": "script", "enhanced_dialogue": True},
            }
            await builder.save_draft(db, org_id, "t", payload, None)
            await builder.publish_draft(db, org_id, "t", None)
        return conversation.id


class Call:
    """One synthetic phone call through the live voice decision layer."""

    def __init__(self, fac: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID,
                 tools: Tools, *, org_id: uuid.UUID = ORG_A, user_id: uuid.UUID = USER_A) -> None:
        self.fac, self.conversation_id, self.tools = fac, conversation_id, tools
        self.org_id, self.user_id = org_id, user_id
        self.done: set[str] = set()
        self.transcript: list[tuple[str, str | None]] = []

    async def say(self, text: str) -> VoiceReply | None:
        plan = await compute_voice_plan(self.org_id, self.user_id, self.conversation_id, text)
        if plan is None:
            self.transcript.append((text, None))
            return None
        reply = await apply_voice_plan(
            plan, org_id=self.org_id, user_id=self.user_id, conversation_id=self.conversation_id,
            text=text, dispatch=self.tools, done_actions=self.done,
        )
        self.transcript.append((text, reply.instruction if reply else None))
        return reply

    async def memory(self) -> dict[str, str]:
        async with self.fac() as db:
            return await load_memory(db, self.org_id, self.user_id)

    async def tags(self) -> list[Any]:
        async with self.fac() as db:
            lead = (await db.execute(select(Lead).where(Lead.org_id == self.org_id, Lead.user_id == self.user_id))).scalar_one()
            return list(lead.tags or [])

    async def appointments(self) -> int:
        async with self.fac() as db:
            return await db.scalar(select(func.count()).select_from(Appointment)) or 0


async def _call(fac: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, tools: Tools | None = None,
                *, llm: Any = None, **seed: Any) -> Call:
    conversation_id = await _seed(fac, **seed)
    monkeypatch.setattr(voice_decision, "_default_llm", lambda api_key: llm or _llm())
    return Call(fac, conversation_id, tools or Tools())


def _instruction(reply: VoiceReply | None) -> str:
    assert reply is not None, "the decision layer should have steered this turn"
    return reply.instruction


# --- 1. who is calling ----------------------------------------------------------------------------------------------------


async def test_1_who_is_calling_is_answered_plainly_as_an_ai(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Aap kaun bol rahe ho?"))
    assert "AI assistant" in text and "why you called" in text and "Do not invent details" in text
    assert call.tools.calls == []


# --- 2 & 3. investment and profit, also asked mid-explanation ---------------------------------------------------------


async def test_2_investment_is_answered_from_approved_facts_only(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Investment kitna lagega?"))
    assert "Answer using ONLY these approved facts" in text
    assert "franchise team shares the exact figure" in text
    assert "answer it first" in text
    assert not re.search(r"\d", text.split("Approved facts:")[1].split("Already known")[0])  # no invented numbers


async def test_3_profit_with_no_approved_figure_is_not_answered_with_one(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch, facts=False)  # the business approved nothing about profit
    reply = await call.say("Beech mein ek baat: profit kitna hoga?")
    text = _instruction(reply)
    assert "do not state, estimate or hint at any number" in text
    assert "Do not guess" in text and reply.tool_choice_none  # the follow-up was raised by the engine
    assert call.tools.names() == ["transfer_to_human"]
    assert not re.search(r"\d+\s*%|\blakh\b|\bcrore\b", text)


async def test_3b_the_unanswerable_question_raises_one_follow_up_not_many(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch, facts=False)
    await call.say("Profit kitna hoga?")
    second = _instruction(await call.say("Profit kitna hoga batao?"))
    assert call.tools.names() == ["transfer_to_human"]  # the team already has the question
    assert "already has the question" in second and "do not guess" in second.lower()


# --- 4. a correction ------------------------------------------------------------------------------------------------------------


async def test_4_a_correction_silently_replaces_the_stored_fact(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    await call.say("Meri dukaan 500 sq ft ki hai.")
    assert (await call.memory())["property_info"] == "Meri dukaan 500 sq ft ki hai"
    text = _instruction(await call.say("Nahi nahi, dukaan 800 sq ft ki hai, 500 galat tha."))
    assert "before it was" not in text and "Quietly use the new value" in text
    memory = await call.memory()
    assert memory["property_info"] == "dukaan 800 sq ft ki hai"
    assert "property_info_pending" not in memory


async def test_4b_a_contradiction_without_a_correction_is_absorbed_not_asked_about(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    await call.say("Meri dukaan 500 sq ft ki hai.")
    text = _instruction(await call.say("Meri dukaan 800 sq ft ki hai."))
    assert "Ask politely which one is correct" not in text
    memory = await call.memory()
    assert memory["property_info"] == "Meri dukaan 800 sq ft ki hai" and "property_info_pending" not in memory


# --- 5. no background -----------------------------------------------------------------------------------------------------------------


async def test_5_no_prior_background_is_acknowledged_without_a_claim_either_way(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Mera pharma ka koi background nahi hai."))
    assert "no prior background" in text and "do not say whether experience is required" in text
    assert "background" in (await call.memory())["customer_background"]


# --- 6 & 7. uncertainty and pressure --------------------------------------------------------------------------------------------


async def test_6_uncertainty_is_met_with_information_not_a_push(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Mujhe abhi sure nahi hai."))
    assert "still deciding" in text and "no hurry" in text
    assert "Do not ask for a meeting" in text
    assert call.tools.calls == [] and await call.appointments() == 0
    assert "disposition:undecided" in await call.tags()


async def test_7_a_worry_about_pressure_is_reassured(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Aap log baad mein pressure to nahi doge na?"))
    assert "no pressure" in text and "in their own time" in text and "Do not ask for a meeting" in text
    assert (await call.memory())["pressure_concern"] == "1"


async def test_7b_the_meeting_commitment_question_is_answered_from_the_approved_fact(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Meeting karne ke baad koi commitment toh nahi hoga?"))
    assert "The first meeting is informational. It does not commit you to anything." in text
    assert "Never promise there is no obligation" not in text or "answer only from the approved facts" in text
    assert call.tools.calls == []


async def test_7c_without_an_approved_fact_no_promise_about_obligation_is_made(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch, facts=False)
    text = _instruction(await call.say("Meeting karne ke baad koi commitment toh nahi hoga?"))
    assert "Never promise there is no obligation" in text and "never state fees" in text
    assert "Answer using ONLY" not in text  # nothing approved to answer from


# --- 8, 9, 10. online meeting, unavailable tomorrow, declined time --------------------------------------------------------


async def test_8_an_online_meeting_preference_is_accepted_and_remembered(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Mujhe online meeting prefer hai."))
    assert "online meeting" in text and "do not fix any time" in text
    assert (await call.memory())["meeting_preference"] == "online"


async def test_9_travelling_tomorrow_means_nothing_is_scheduled_for_tomorrow(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Kal travel karunga."))
    assert "Do not book or schedule it" in text and "which other day or time suits them" in text
    assert call.tools.calls == [] and await call.appointments() == 0
    assert (await call.memory())["unavailable_dates"] == TOMORROW


async def test_10_a_declined_time_is_not_booked_and_asks_for_another(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Us time par nahi ho payega."))
    assert "does not work" in text and "Do not book or schedule" in text
    assert call.tools.calls == []


async def test_10b_a_declined_time_with_a_new_one_in_the_same_sentence_takes_the_new_one(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    reply = await call.say("Kal 5 baje nahi ho payega, parso 6 baje call karna.")
    assert call.tools.names() == ["request_callback"]
    args = call.tools.calls[0][1]
    assert args["date"] == (date.today() + timedelta(days=2)).isoformat() and args["time"] == "18:00"
    assert "callback is now scheduled" in _instruction(reply)


# --- 11. no repeated explanations -----------------------------------------------------------------------------------------------


async def test_11_a_margin_explanation_already_given_is_not_repeated(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    first = _instruction(await call.say("Margin kitna milta hai?"))
    assert "Margins depend on the product mix" in first and "already given this earlier" not in first
    assert "margin" in (await call.memory())["answered_topics"] or "profit" in (await call.memory())["answered_topics"]
    second = _instruction(await call.say("Aur margin kitna hota hai?"))
    assert "already given this earlier" in second and "Do not repeat the full explanation" in second


async def test_11b_a_complaint_about_repetition_is_apologised_for_and_the_agent_moves_on(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    text = _instruction(await call.say("Aap baar-baar wahi baat kyun pooch rahe hain?"))
    assert "Apologise in a few words" in text and "do not repeat" in text.lower()
    assert call.tools.calls == []


# --- 12. appointments are only confirmed when the tool confirmed them ------------------------------------------------------------


async def test_12_a_booking_is_confirmed_only_after_the_tool_succeeds(factory, monkeypatch) -> None:
    ok = await _call(factory, monkeypatch, Tools(book_appointment={"status": "ok", "appointment_id": "a1"}))
    text = _instruction(await ok.say("Meeting book kar do parso shaam 5 baje"))
    assert ok.tools.names() == ["book_appointment"] and "is now booked" in text
    assert "disposition:meeting_confirmed" in await ok.tags()


async def test_12b_if_the_tool_fails_nothing_is_confirmed(factory, monkeypatch) -> None:
    bad = await _call(factory, monkeypatch, Tools(book_appointment={"status": "error", "reason": "slot_conflict"}))
    reply = await bad.say("Meeting book kar do parso shaam 5 baje")
    assert reply is None  # no instruction at all: the call carries on without a false confirmation
    tags = await bad.tags()
    assert "disposition:meeting_confirmed" not in tags


async def test_12c_a_proposed_meeting_is_proposed_not_confirmed(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    await call.say("Haan interested hoon, aur details batao")
    assert call.tools.calls == [] and await call.appointments() == 0
    assert "disposition:meeting_proposed" in await call.tags()
    assert "disposition:meeting_confirmed" not in await call.tags()


async def test_12d_an_undecided_customer_is_not_pushed_or_marked_confirmed(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    await call.say("Mujhe abhi sure nahi hai.")
    text = _instruction(await call.say("Haan interested hoon"))
    assert "Do not push for a meeting" in text
    tags = await call.tags()
    assert tags == ["disposition:undecided"] or tags == ["disposition:interested"]
    assert call.tools.calls == []


# --- the whole call ---------------------------------------------------------------------------------------------------------------


async def test_the_whole_synthetic_call_ends_undecided_with_nothing_booked(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    turns = [
        "Aap kaun bol rahe ho?",
        "Investment kitna lagega?",
        "Aur profit kitna hoga?",
        "Meri dukaan 500 sq ft ki hai.",
        "Nahi nahi, dukaan 800 sq ft ki hai, 500 galat tha.",
        "Mera pharma ka koi background nahi hai.",
        "Mujhe abhi sure nahi hai.",
        "Aap log baad mein pressure to nahi doge na?",
        "Meeting karne ke baad koi commitment toh nahi hoga?",
        "Mujhe online meeting prefer hai.",
        "Kal travel karunga.",
        "Us time par nahi ho payega.",
        "Aap baar-baar wahi baat kyun pooch rahe hain?",
        "Aur margin kitna hota hai?",
        "Theek hai, thank you, bye.",
    ]
    for turn in turns:
        await call.say(turn)
    steered = [i for _, i in call.transcript if i]
    assert len(steered) == len(turns), "every turn was steered by the decision layer"
    assert call.tools.calls == [] and await call.appointments() == 0  # nothing booked, nothing scheduled
    assert await call.tags() == ["disposition:undecided"]
    memory = await call.memory()
    assert memory["property_info"] == "dukaan 800 sq ft ki hai"
    assert memory["meeting_preference"] == "online" and memory["unavailable_dates"] == TOMORROW
    assert not any(re.search(r"\b(is now booked|callback is now scheduled)\b", i) for i in steered)
    assert "Close naturally" in steered[-1]
    # No instruction invents a number the business never approved.
    for instruction in steered:
        assert not re.search(r"\d+\s*%|\d\s*(lakh|crore)", instruction)


# --- text channel: same policy, template replies, same CRM outcome ---------------------------------------------------------------------


def _text_llm(scenario: str = "interested"):
    """Classifies when asked to; refuses to phrase, so the engine uses the approved text verbatim."""
    classify = _llm(scenario)

    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        if "You classify" in messages[0]["content"]:
            return await classify(messages)
        raise RuntimeError("phrasing unavailable")

    return call


async def _text_turn(fac, text: str, tools: Tools, *, org_id=ORG_A, user_id=USER_A, conversation_id=None, llm=None):
    async with fac() as db:
        return await run_engine(
            db, org_id=org_id, user_id=user_id, conversation_id=conversation_id, channel="whatsapp",
            text=text, history=[], api_key=None, dispatch=tools, llm=llm or _text_llm(),
        )


async def test_text_channel_hesitation_gets_a_gentle_reply_and_no_pushy_next_step(factory) -> None:
    conversation_id = await _seed(factory, channel="whatsapp")
    tools = Tools()
    result = await _text_turn(factory, "Mujhe abhi sure nahi hai.", tools, conversation_id=conversation_id)
    assert result.reply and "jaldi nahi" in result.reply and "call" not in result.reply.lower()
    assert tools.calls == []
    async with factory() as db:
        lead = (await db.execute(select(Lead).where(Lead.user_id == USER_A))).scalar_one()
        assert lead.tags == ["disposition:undecided"]
        turn = (await db.execute(select(ConversationTurn).where(ConversationTurn.user_id == USER_A))).scalars().one()
        assert turn.action_id == "none" and turn.fallback_reason is None


async def test_text_channel_answer_does_not_tack_on_a_call_request_for_an_undecided_customer(factory) -> None:
    conversation_id = await _seed(factory, channel="whatsapp")
    tools = Tools()
    async with factory() as db:  # an earlier turn made them undecided
        from apps.api.core.conversation.memory import save_facts

        await save_facts(db, ORG_A, USER_A, {"interest_level": "undecided"})
        await db.commit()
    result = await _text_turn(factory, "Haan interested hoon, meeting ke baare mein batao", tools, conversation_id=conversation_id)
    assert result.reply and "The first meeting is informational" in result.reply
    assert "quick call" not in result.reply and "call set kar" not in result.reply


async def test_text_channel_flag_off_keeps_the_old_pushy_next_step(factory) -> None:
    conversation_id = await _seed(factory, channel="whatsapp", enhanced=False)
    result = await _text_turn(factory, "Haan interested hoon, meeting ke baare mein batao", Tools(), conversation_id=conversation_id)
    assert result.reply and "The first meeting is informational" in result.reply
    assert "quick call" in result.reply or "call set kar" in result.reply  # the old nudge, unchanged


# --- isolation and opt-in --------------------------------------------------------------------------------------------------------------------


async def test_the_mode_is_per_org_and_nothing_leaks_between_orgs(factory, monkeypatch) -> None:
    conversation_a = await _seed(factory, org_id=ORG_A, user_id=USER_A, enhanced=True)
    conversation_b = await _seed(factory, org_id=ORG_B, user_id=USER_B, enhanced=False)
    monkeypatch.setattr(voice_decision, "_default_llm", lambda api_key: _llm("objection"))
    a = Call(factory, conversation_a, Tools(), org_id=ORG_A, user_id=USER_A)
    b = Call(factory, conversation_b, Tools(), org_id=ORG_B, user_id=USER_B)

    text_a = _instruction(await a.say("Mujhe abhi sure nahi hai."))
    text_b = _instruction(await b.say("Mujhe abhi sure nahi hai."))
    assert "still deciding" in text_a and "still deciding" not in text_b  # org B behaves as before
    assert "Keep it short and conversational" in text_a and "Keep it short and conversational" not in text_b

    assert "disposition:undecided" in await a.tags() and await b.tags() == []
    memory_a, memory_b = await a.memory(), await b.memory()
    assert memory_a.get("interest_level") == "undecided" and "pressure_concern" not in memory_b
    assert "interest_level" not in memory_b or memory_b["interest_level"] != "undecided"
    async with factory() as db:
        rows = (await db.execute(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.org_id == ORG_B))).scalar()
        assert rows == 1


# --- decision-layer speed, timeouts and fallbacks ----------------------------------------------------------------------------------------------------


async def test_rules_settle_the_common_cases_so_no_model_call_is_made(factory, monkeypatch) -> None:
    calls: list[str] = []
    call = await _call(factory, monkeypatch, llm=_llm(calls=calls))
    for text in ("Mujhe abhi sure nahi hai.", "Kal travel karunga.", "Aap kaun bol rahe ho?", "Investment kitna lagega?"):
        await call.say(text)
    assert calls == []


async def test_decision_latency_and_outcomes_are_recorded(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    await call.say("Mujhe abhi sure nahi hai.")
    await call.say("Investment kitna lagega?")
    snapshot = runtime_stats.snapshot()["voice"]
    assert snapshot["decisions"] == 2 and snapshot["outcomes"] == {"ok": 2}
    assert snapshot["fallback_rate"] == 0.0 and snapshot["latency_ms"]["n"] == 2
    assert snapshot["latency_ms"]["p50"] is not None


async def test_a_model_timeout_falls_back_and_never_breaks_the_call(factory, monkeypatch) -> None:
    async def slow(messages: list[dict[str, Any]]) -> ChatResult:
        await asyncio.sleep(5)
        return ChatResult(content="{}", tokens_in=0, tokens_out=0)

    monkeypatch.setattr(settings, "conversation_voice_llm_timeout_seconds", 0.05)
    call = await _call(factory, monkeypatch, llm=slow)
    started = time.monotonic()
    reply = await call.say("zebra quantum marmalade")  # nothing the rules recognise
    assert reply is None and time.monotonic() - started < 1.0
    stats = runtime_stats.snapshot()["voice"]
    assert stats["outcomes"] == {"timeout": 1} and stats["fallback_rate"] == 1.0


async def test_a_model_error_falls_back(factory, monkeypatch) -> None:
    async def broken(messages: list[dict[str, Any]]) -> ChatResult:
        raise RuntimeError("provider down")

    call = await _call(factory, monkeypatch, llm=broken)
    assert await call.say("zebra quantum marmalade") is None
    assert runtime_stats.snapshot()["voice"]["outcomes"] == {"llm_error": 1}


async def test_a_bug_in_the_policy_cannot_end_a_call(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("policy bug")

    monkeypatch.setattr(dialogue, "enhance_decision", boom)
    assert await call.say("Mujhe abhi sure nahi hai.") is None  # plan dropped, the call carries on as today
    assert runtime_stats.snapshot()["voice"]["outcomes"] == {"error": 1}


async def test_a_decision_that_misses_the_realtime_budget_is_recorded_and_dropped(monkeypatch) -> None:
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 0.05)
    voice_decision.set_cached_voice_mode(ORG_A, "live")
    state = adapter.CallState(user_id=USER_A, conversation_id=uuid.uuid4(), org_id=ORG_A)

    async def late() -> None:
        await asyncio.sleep(2)

    state.voice_decision_task = asyncio.create_task(late())
    state.voice_decision_started = time.monotonic()
    state.voice_decision_text = "x"
    assert await adapter._voice_decision_response(state) is None
    assert runtime_stats.snapshot()["voice_budget"]["outcomes"] == {"budget_timeout": 1}
    assert state.voice_decision_task is None


# --- compatibility with the Phase 3 interruption path -------------------------------------------------------------------------------------------------


async def test_an_interruption_keeps_the_enhanced_instructions_and_adds_the_priority_rule(factory, monkeypatch) -> None:
    call = await _call(factory, monkeypatch)
    instruction = _instruction(await call.say("Investment kitna lagega?"))
    state = adapter.CallState(user_id=USER_A, conversation_id=call.conversation_id, org_id=ORG_A, base_instructions="BASE")
    merged = adapter._with_priority({"instructions": f"BASE\n\n{instruction}"}, state)["instructions"]
    assert merged.startswith("BASE\n\nDECISION FOR THIS TURN") and "franchise team shares the exact figure" in merged
    assert merged.endswith(adapter.INTERRUPT_PRIORITY_INSTRUCTION)  # both rules reach the model, in order
