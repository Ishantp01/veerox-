"""Conformance tests against the architecture blueprint
("WorkAssignAI - Human-Like AI Calling & Conversation Intelligence Engine").

One test (or group) per blueprint section, named after it, so a reviewer can
read the PDF next to this file. Pure engine tests: no network, the model is a
fake wherever one is needed.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation import reply as reply_module
from apps.api.core.conversation import simulate as simulate_module
from apps.api.core.conversation.config import (
    SMART_SCRIPT_ENGINE_SCENARIOS,
    default_config,
)
from apps.api.core.conversation.decide import validate_action
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.simulate import simulate_turn
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    MEMORY_KEYS,
    SCENARIOS,
)
from apps.api.core.conversation.understand import classify_with_rules
from apps.api.core.llm import ChatResult
from apps.api.db.models import Conversation, Org, User
from apps.api.db.models.conversation_engine import ConversationMemory, KnowledgeItem

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")
TODAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _pin_default_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_ID))


async def _seed(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Blueprint Org", conversation_mode="human_conversation"))
    db.add(Org(id=OTHER_ORG_ID, name="Other Org"))
    await db.commit()
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="T"))
    await db.commit()
    db.add(Conversation(org_id=ORG_ID, user_id=USER_ID, channel="whatsapp"))
    await db.commit()


def _fake_model(monkeypatch: pytest.MonkeyPatch, content: str | None, *, raises: bool = False) -> None:
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        if raises:
            raise RuntimeError("provider down")
        return ChatResult(content=content, tokens_in=5, tokens_out=5)

    monkeypatch.setattr(simulate_module, "_default_llm", lambda api_key: call)


async def _turn(db: AsyncSession, text: str, *, state=None, memory=None, use_llm=False, history=None):
    return await simulate_turn(
        db,
        ORG_ID,
        text=text,
        config=default_config(),
        state=state,
        memory=memory or {},
        history=history or [],
        use_llm=use_llm,
        api_key=None,
        smart_script=False,
    )


# --- §4 / §7: the six concepts and the initial scenario library --------------------------------

LIBRARY_PHRASES = [
    ("hello", "greeting"),
    ("yes speaking", "identification"),
    ("we run a travel agency", "discovery"),
    ("I am interested, tell me more", "interested"),
    ("what is the price", "price_question"),
    ("that is too expensive", "objection"),
    ("any discount available", "discount_request"),
    ("I am in a meeting", "busy_callback"),  # the blueprint's own §6 example
    ("I am busy", "busy_callback"),  # §6
    ("Call later", "busy_callback"),  # §6
    ("please call me at 6 pm", "callback_request"),
    ("send the details on whatsapp", "whatsapp_info_request"),
    ("I am not interested", "not_interested"),
    ("this is useless", "angry"),
    ("I don't understand", "confused"),
    ("I want to book a demo tomorrow at 5 pm", "appointment_request"),
    ("I want to talk to a human", "human_handoff"),
    ("thank you, bye", "closing"),
]


@pytest.mark.parametrize(("text", "expected"), LIBRARY_PHRASES)
def test_s7_every_library_scenario_is_recognised(text: str, expected: str) -> None:
    result = classify_with_rules(text, state=None, today=TODAY, config=default_config())
    assert result is not None, text
    assert result.scenario == expected, (text, result.scenario)
    assert 0.0 <= result.confidence <= 1.0  # §8: every decision carries a confidence


def test_s7_library_has_all_seventeen_named_scenarios() -> None:
    ids = set(default_config().ids())
    assert len(ids) == 17
    assert "unknown_question" in ids and "unknown_question" in SCENARIOS


async def test_s7_unknown_question_is_a_scenario_that_never_guesses(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _fake_model(monkeypatch, json.dumps({"scenario": "unknown_question", "intent": "q", "emotion": "neutral",
                                         "confidence": 0.9, "facts": {}, "action_id": "none"}))
    result = await _turn(db_session, "do you ship to Mars", use_llm=True)
    assert result["scenario"] == "unknown_question"
    assert result["action_id"] == "transfer_to_human"  # §11: acknowledge the limit, hand over
    assert result["knowledge"] == []


# --- §8: structured interpretation + confidence, clarification / fallback when unsure ---------


async def test_s8_interpretation_is_structured(db_session: AsyncSession) -> None:
    await _seed(db_session)
    result = await _turn(db_session, "I am busy, call me later today at 6 pm")
    for key in ("intent", "scenario", "confidence", "emotion", "facts", "action_id", "state_after"):
        assert key in result
    assert result["facts"]["callback_time_hhmm"] == "18:00"  # entity extraction


async def test_s8_low_confidence_falls_back_instead_of_acting(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _fake_model(monkeypatch, json.dumps({"scenario": "interested", "intent": "x", "emotion": "neutral",
                                         "confidence": 0.3, "facts": {}, "action_id": "none"}))
    result = await _turn(db_session, "purple weather banana", use_llm=True)  # no rule matches
    assert result["fallback_reason"] == "low_confidence"
    assert result["reply"] is None and result["handled_by"] == "script"


# --- §18: fallback + validation: bad model output and unapproved actions never run ------------


@pytest.mark.parametrize(
    ("content", "raises", "reason"),
    [("this is not json", False, "invalid_output"), (None, False, "invalid_output"), ("x", True, "llm_error")],
)
async def test_s18_model_trouble_falls_back_to_the_script(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, content: str | None, raises: bool, reason: str
) -> None:
    await _seed(db_session)
    _fake_model(monkeypatch, content, raises=raises)
    result = await _turn(db_session, "do you ship to Mars", use_llm=True)
    assert result["fallback_reason"] == reason and result["reply"] is None


async def test_s18_model_can_only_request_an_approved_action(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    _fake_model(monkeypatch, json.dumps({"scenario": "interested", "intent": "x", "emotion": "neutral",
                                         "confidence": 0.95, "facts": {}, "action_id": "drop_database"}))
    result = await _turn(db_session, "tell me something odd", use_llm=True)
    assert result["fallback_reason"] == "invalid_action" and result["action_id"] is None
    assert "drop_database" not in ALLOWED_ACTIONS
    assert not validate_action("request_callback", {})  # required arguments are enforced too
    assert validate_action("request_callback", {"date": "2026-10-16", "time": "17:00"})


def test_s18_action_allow_list_is_exactly_the_existing_tools() -> None:
    assert ALLOWED_ACTIONS == {
        "none", "request_callback", "transfer_to_human", "mark_not_interested",
        "book_appointment", "send_whatsapp_message",
    }


# --- §9: state is not a rigid line; any state can jump -----------------------------------------


@pytest.mark.parametrize("state", ["new", "engaged", "discovery", "qualification", "awaiting_callback_time", "callback_set"])
async def test_s9_any_state_can_jump_to_busy_price_anger_or_confusion(
    db_session: AsyncSession, state: str
) -> None:
    await _seed(db_session)
    for text, scenario in [
        ("I am busy, call me tomorrow", "busy_callback"),
        ("what is the price", "price_question"),
        ("this is useless", "angry"),
        ("I don't understand", "confused"),
    ]:
        result = await _turn(db_session, text, state=state)
        assert result["scenario"] == scenario, (state, text, result["scenario"])


async def test_s9_state_is_stored_separately_from_the_reply(db_session: AsyncSession) -> None:
    await _seed(db_session)
    result = await _turn(db_session, "I am busy, call me later")
    assert result["state_after"] == "awaiting_callback_time" and result["reply"]


# --- §10: structured memory with creation, update and expiry rules ------------------------------


async def test_s10_only_structured_facts_are_stored_and_they_expire(db_session: AsyncSession) -> None:
    await _seed(db_session)
    await save_facts(
        db_session, ORG_ID, USER_ID,
        {"name": "Rahul", "business_type": "Travel Agency", "pain_point": "Manual lead follow-up",
         "preferred_channel": "whatsapp", "callback_time": "2026-10-16 18:00",
         "random_chatter": "I had tea this morning"},  # must be ignored
    )
    await db_session.commit()
    memory = await load_memory(db_session, ORG_ID, USER_ID)
    assert set(memory) == {"name", "business_type", "pain_point", "preferred_channel", "callback_time"}
    assert set(memory) <= set(MEMORY_KEYS)
    from sqlalchemy import select

    scopes = {r.key: (r.scope, r.expires_at) for r in (await db_session.execute(select(ConversationMemory))).scalars()}
    assert scopes["callback_time"][0] == "call"  # temporary, call-scoped
    assert scopes["name"][0] == "persistent"  # customer preference, kept per data policy
    assert all(expires is not None for _, expires in scopes.values())  # everything expires


async def test_s10_memory_updates_and_is_used_not_asked_again(db_session: AsyncSession) -> None:
    await _seed(db_session)
    first = await _turn(db_session, "my name is Rahul, we run a travel agency, our problem is manual follow-up")
    assert first["memory_after"]["business_type"] == "Travel Agency"
    assert "manual follow-up" in first["memory_after"]["pain_point"]
    second = await _turn(db_session, "I am interested", memory=first["memory_after"], state=first["state_after"])
    assert second["memory_after"]["name"] == "Rahul"  # carried forward


# --- §11: knowledge and unknown questions --------------------------------------------------------


async def test_s11_answers_only_from_approved_knowledge(db_session: AsyncSession) -> None:
    await _seed(db_session)
    db_session.add(KnowledgeItem(org_id=ORG_ID, kind="pricing", title="Starter plan price",
                                 body="Starter is Rs 999 per month.", keywords="price starter"))
    db_session.add(KnowledgeItem(org_id=OTHER_ORG_ID, kind="pricing", title="Other price",
                                 body="Secret plan is Rs 1 per month.", keywords="price"))
    await db_session.commit()
    result = await _turn(db_session, "what is the starter plan price?")
    assert result["scenario"] == "price_question"
    assert "Rs 999" in result["reply"] and "Rs 1 " not in result["reply"]  # §23: other tenant's text unreachable
    assert [k["title"] for k in result["knowledge"]] == ["Starter plan price"]


async def test_s11_no_approved_answer_means_no_invented_price(db_session: AsyncSession) -> None:
    await _seed(db_session)
    result = await _turn(db_session, "what is the price")
    assert result["action_id"] == "transfer_to_human" and "confirm" in result["reply"].lower()
    assert not any(ch.isdigit() for ch in result["reply"])  # no number appears from nowhere


# --- §5 / §17: human-like, voice-friendly replies ------------------------------------------------


def test_s5_replies_are_short_and_ask_one_question_at_a_time() -> None:
    for kind, by_language in reply_module._TEMPLATES.items():
        for language, text in by_language.items():
            assert len(text) <= 190, (kind, language, len(text))
            assert text.count("?") + text.count("？") <= 1, (kind, language, text)
            assert text.count(".") + text.count("!") + text.count("।") <= 3, (kind, language)


def test_s5_replies_exist_in_hindi_english_and_hinglish() -> None:
    for kind, by_language in reply_module._TEMPLATES.items():
        assert {"en", "hi", "hinglish"} <= set(by_language), kind


# --- §6: the Customer Busy scenario behaves exactly as described -----------------------------------


async def test_s6_customer_busy_pauses_the_pitch_and_secures_a_callback(db_session: AsyncSession) -> None:
    await _seed(db_session)
    busy = default_config().get("busy_callback")
    assert busy is not None
    assert "Continuing the pitch" in busy.must_avoid  # "Avoid: continuing the sales pitch"
    assert {"none", "request_callback"} <= set(busy.actions)
    result = await _turn(db_session, "I am in a meeting")
    assert result["scenario"] == "busy_callback" and result["action_id"] == "none"
    assert "back" in result["reply"].lower() and "time" in result["reply"].lower()  # asks a callback time


# --- §29: the blueprint's worked example, word for word ----------------------------------------


async def test_s29_end_to_end_example_conversation(db_session: AsyncSession) -> None:
    await _seed(db_session)
    hello = await _turn(db_session, "Hello.")
    assert hello["scenario"] == "greeting"

    busy = await _turn(db_session, "Haan bataiye, lekin main abhi thoda busy hoon.",
                       state=hello["state_after"], memory=hello["memory_after"])
    assert busy["scenario"] == "busy_callback"  # CUSTOMER_BUSY: the pitch is paused
    assert busy["action_id"] == "none" and busy["state_after"] == "awaiting_callback_time"
    assert busy["language"] == "hinglish"

    evening = await _turn(db_session, "Shaam ko 6 baje.", state=busy["state_after"], memory=busy["memory_after"])
    assert evening["scenario"] == "busy_callback"
    assert evening["action_id"] == "request_callback"  # the permitted CRM follow-up action
    assert evening["action_args"]["time"] == "18:00"  # "Shaam ko 6 baje" -> 18:00
    assert evening["memory_after"]["callback_time"].endswith("18:00")  # memory updated
    assert evening["state_after"] == "callback_set"  # moves toward closing
    assert "18:00" in evening["reply"]  # the AI confirms the time back


# --- §23: tenant isolation -------------------------------------------------------------------------


async def test_s23_scenarios_and_memory_never_cross_tenants(db_session: AsyncSession) -> None:
    from apps.api.core.conversation import builder
    from apps.api.core.conversation.config import load_active_config

    await _seed(db_session)
    other_scenarios = [s.to_json() for s in default_config().scenarios.values()]
    for s in other_scenarios:
        if s["id"] == "closing":
            s["enabled"] = False
    await builder.save_draft(db_session, OTHER_ORG_ID, "other-admin", {"scenarios": other_scenarios}, None)
    await builder.publish_draft(db_session, OTHER_ORG_ID, "other-admin", None)
    mine = await load_active_config(db_session, ORG_ID)
    theirs = await load_active_config(db_session, OTHER_ORG_ID)
    assert mine.is_enabled("closing") and not theirs.is_enabled("closing")

    other_user = uuid.uuid4()
    db_session.add(User(id=other_user, org_id=OTHER_ORG_ID, phone="+910000000009", name="O"))
    await db_session.commit()
    await save_facts(db_session, OTHER_ORG_ID, other_user, {"name": "Secret"})
    await db_session.commit()
    assert await load_memory(db_session, ORG_ID, USER_ID) == {}


# --- §19 / §20: shadow mode and per-org rollout switches -------------------------------------------


def test_s20_each_org_has_independent_mode_and_voice_switches() -> None:
    from apps.api.db.models.org import CONVERSATION_MODES, CONVERSATION_VOICE_MODES

    assert CONVERSATION_MODES == ("strict_script", "smart_script", "human_conversation")
    assert CONVERSATION_VOICE_MODES == ("off", "shadow", "live")  # shadow = analyse, never speak


def test_s12_smart_script_engine_only_acts_where_it_is_safe() -> None:
    assert SMART_SCRIPT_ENGINE_SCENARIOS == {
        "human_handoff", "angry", "not_interested", "busy_callback", "callback_request",
        "appointment_request", "whatsapp_info_request", "closing",
    }


# --- §16: cost and latency: rules first, no model call for clear cases ---------------------------------


@pytest.mark.parametrize(("text", "expected"), LIBRARY_PHRASES)
async def test_s16_clear_cases_never_call_the_model(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, text: str, expected: str
) -> None:
    await _seed(db_session)

    async def boom(messages: list[dict[str, Any]]) -> ChatResult:
        raise AssertionError("the model must not be called for a clear case")

    monkeypatch.setattr(simulate_module, "_default_llm", lambda api_key: boom)
    result = await _turn(db_session, text, use_llm=True)
    assert result["source"] == "rules" and result["scenario"] == expected


# --- §10: team_size and interest_level are remembered -----------------------------------------------


def test_s10_team_size_and_interest_level_are_extracted() -> None:
    result = classify_with_rules(
        "we run a travel agency, team of 10, our problem is manual follow-up",
        state=None, today=TODAY, config=default_config(),
    )
    assert result is not None and result.facts["team_size"] == "10"
    warm = classify_with_rules("mere paas 25 log hain, interested", state=None, today=TODAY, config=default_config())
    assert warm is not None and warm.facts["team_size"] == "25" and warm.facts["interest_level"] == "high"
    cold = classify_with_rules("not interested", state=None, today=TODAY, config=default_config())
    assert cold is not None and cold.facts["interest_level"] == "low"
    other = classify_with_rules("what is the price", state=None, today=TODAY, config=default_config())
    assert other is not None and other.facts["interest_level"] == "medium"


async def test_s10_blueprint_memory_example_fits_the_stored_keys(db_session: AsyncSession) -> None:
    await _seed(db_session)
    await save_facts(
        db_session, ORG_ID, USER_ID,
        {"name": "Rahul", "business_type": "Travel Agency", "pain_point": "Manual lead follow-up",
         "team_size": "10", "preferred_channel": "WhatsApp", "callback_time": "18:00", "interest_level": "high"},
    )
    await db_session.commit()
    stored = await load_memory(db_session, ORG_ID, USER_ID)
    # The seven fields of the blueprint's section 10 example.
    assert set(stored) == {"name", "business_type", "pain_point", "team_size", "preferred_channel",
                           "callback_time", "interest_level"}


# --- §8: low confidence -> clarify, hand off, or fall back, by business configuration --------------


def _config_with(action: str):
    from apps.api.core.conversation.config import config_from_json

    return config_from_json(
        {"scenarios": [s.to_json() for s in default_config().scenarios.values()],
         "settings": {"low_confidence_action": action}}
    )


async def _unsure(db: AsyncSession, monkeypatch: pytest.MonkeyPatch, action: str, *, smart: bool = False):
    _fake_model(monkeypatch, json.dumps({"scenario": "interested", "intent": "x", "emotion": "neutral",
                                         "confidence": 0.3, "facts": {}, "action_id": "none"}))
    return await simulate_turn(
        db, ORG_ID, text="purple weather banana", config=_config_with(action), state="engaged",
        memory={}, history=[], use_llm=True, api_key=None, smart_script=smart,
    )


async def test_s8_default_is_the_safe_script_fallback(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    await _seed(db_session)
    result = await _unsure(db_session, monkeypatch, "script")
    assert result["fallback_reason"] == "low_confidence" and result["reply"] is None


async def test_s8_clarify_asks_one_short_question_and_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    result = await _unsure(db_session, monkeypatch, "clarify")
    assert result["fallback_reason"] is None and result["action_id"] == "none"
    assert result["reply_kind"] == "clarify" and result["reply"].count("?") == 1
    assert result["state_after"] == "engaged"  # the conversation stays where it was


async def test_s8_handoff_gives_the_question_to_a_person(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    result = await _unsure(db_session, monkeypatch, "handoff")
    assert result["action_id"] == "transfer_to_human" and result["state_after"] == "handed_off"
    assert "team" in result["reply"].lower()


async def test_s8_smart_script_never_improvises_when_unsure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    result = await _unsure(db_session, monkeypatch, "clarify", smart=True)
    assert result["fallback_reason"] == "low_confidence" and result["reply"] is None


# --- §13: structure an existing script, without touching it -------------------------------------------

SCRIPT = """OPENING
Hello, this is Asha from WorkAssign. Is this a good time to talk?

PRODUCT
We help travel agencies follow up with leads automatically on WhatsApp and calls.

PRICING
The Starter plan is Rs 999 per month. The Pro plan is Rs 2499 per month.

OBJECTIONS
If the customer says it is expensive, offer a free 7 day trial.

CLOSING
Book a demo for the customer.
"""


def test_s13_script_is_split_into_labelled_sections() -> None:
    from apps.api.core.conversation.script_import import split_sections

    sections = split_sections(SCRIPT)
    assert [s.title for s in sections] == ["OPENING", "PRODUCT", "PRICING", "OBJECTIONS", "CLOSING"]
    assert [s.kind for s in sections] == ["opening", "product", "pricing", "objections", "closing"]
    assert "Rs 999" in sections[2].text
    plain = split_sections("First paragraph about us.\n\nSecond paragraph about price.")
    assert len(plain) == 2  # a script with no headings splits on blank lines


async def test_s13_suggestions_never_invent_numbers() -> None:
    from apps.api.core.conversation.script_import import suggest_from_script

    model_output = json.dumps(
        {
            "knowledge": [
                {"kind": "pricing", "title": "Starter plan", "body": "Starter is Rs 999 per month.", "keywords": "price, starter, kitna"},
                {"kind": "pricing", "title": "Enterprise plan", "body": "Enterprise is Rs 49999 per month.", "keywords": "enterprise"},
                {"kind": "weird", "title": "Free trial", "body": "A free 7 day trial is available.", "keywords": "trial"},
            ],
            "scenarios": [
                {"name": "Free trial request", "situation": "asks for a trial", "examples": ["can I try it first", "free trial hai kya"],
                 "goal": "Offer the trial", "should_do": "Offer a free 7 day trial.", "must_avoid": "Promising extensions"},
                {"name": "Big discount", "situation": "wants discount", "examples": ["give 50 percent off"],
                 "goal": "Handle it", "should_do": "Offer 50% off.", "must_avoid": ""},
                {"name": "Price question", "situation": "dup", "examples": ["price?"], "goal": "x", "should_do": "", "must_avoid": ""},
            ],
        }
    )

    async def llm(messages: list[dict[str, Any]]) -> ChatResult:
        return ChatResult(content=model_output, tokens_in=1, tokens_out=1)

    result = await suggest_from_script(SCRIPT, existing_scenario_ids={"price_question"}, llm=llm)
    titles = [k["title"] for k in result.knowledge]
    assert titles == ["Starter plan", "Free trial"]  # Rs 49999 is not in the script -> dropped
    assert any("49999" in note for note in result.dropped)
    assert next(k for k in result.knowledge if k["title"] == "Free trial")["kind"] == "faq"  # unknown kind -> faq
    ids = [s["id"] for s in result.scenarios]
    assert ids == ["free_trial_request", "price_question_2"]  # unique ids; the invented "50%" scenario is dropped
    assert any("50" in note for note in result.dropped)
    assert all(s["actions"] == ["none"] for s in result.scenarios)  # suggestions can never add an action
    assert result.model_used and len(result.sections) == 5


async def test_s13_bad_model_output_still_returns_the_sections() -> None:
    from apps.api.core.conversation.script_import import suggest_from_script

    async def llm(messages: list[dict[str, Any]]) -> ChatResult:
        return ChatResult(content="sorry, here is some text", tokens_in=1, tokens_out=1)

    result = await suggest_from_script(SCRIPT, existing_scenario_ids=set(), llm=llm)
    assert len(result.sections) == 5 and result.knowledge == [] and result.model_used is False
    assert result.dropped
    no_model = await suggest_from_script(SCRIPT, existing_scenario_ids=set(), llm=None)
    assert len(no_model.sections) == 5 and not no_model.model_used


# --- §12: platform-wide scenario templates ---------------------------------------------------------------


def test_s12_templates_are_platform_admin_only_for_writes() -> None:
    from apps.api.deps import verify_platform_admin
    from apps.api.routers.conversation_engine_config import router

    write_routes = {
        (route.path, tuple(route.methods))
        for route in router.routes
        if route.path.startswith("/conversation-engine/templates/") and not route.methods == {"GET"}
    }
    assert write_routes, "template write routes exist"
    for route in router.routes:
        if route.path.startswith("/conversation-engine/templates/"):
            deps = [d.call for d in route.dependant.dependencies]
            assert verify_platform_admin in deps, route.path  # org admins cannot change the library
