"""Phase 7C: context-aware conversation (core/conversation/dialogue.py and the memory it feeds), driven through
the same understand -> decide pipeline Phase 4 uses, with no model and no database except where stated.

All conversation fragments are synthetic. These tests prove what the rule layer remembers and tells the speaking
model to do; whether the model then SOUNDS natural, and obeys, needs live calls.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from apps.api.core.conversation import dialogue, voice_decision
from apps.api.core.conversation.dialogue import DialogueState
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.types import MEMORY_KEYS, Decision, Understanding
from apps.api.core.conversation.voice_decision import VoicePlan, build_instruction
from apps.api.db.models import Org, User
from apps.api.tests.test_dialogue import FACT_INVEST, FACT_MEETING, ON, TODAY, _decide, _llm_says

ORG_A = "00000000-0000-0000-0000-0000000000a1"
ORG_B = "00000000-0000-0000-0000-0000000000b2"


class Talk:
    """A synthetic call: each turn runs the real pipeline and feeds its memory updates to the next turn."""

    def __init__(self, memory: dict[str, str] | None = None) -> None:
        self.memory: dict[str, str] = dict(memory or {})
        self.turns: list[tuple[Understanding, Decision]] = []

    async def say(self, text: str, *, knowledge: list[Any] | None = None) -> tuple[Understanding, Decision]:
        und, decision = await _decide(text, memory=self.memory, knowledge=knowledge, llm=_llm_says("other", 0.5))
        updates, forget = dialogue.memory_updates(und, decision, DialogueState.from_memory(self.memory), self.memory)
        remembered = {k: v for k, v in und.facts.items() if k in MEMORY_KEYS}
        self.memory.update(remembered)
        self.memory.update(updates)
        for key in forget:
            self.memory.pop(key, None)
        self.turns.append((und, decision))
        return und, decision

    @property
    def last(self) -> Decision:
        return self.turns[-1][1]


def _directives(decision: Decision) -> str:
    return " ".join(decision.directives)


# --- 1-2. remembering a city and a budget ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("Main Jabalpur se hoon.", "Jabalpur"),
        ("I am from Indore", "Indore"),
        ("Mera shehar Bhopal hai", "Bhopal"),
        ("Main Navi Mumbai mein rehta hoon", "Navi Mumbai"),
    ],
)
async def test_a_city_the_caller_states_is_remembered(text: str, city: str) -> None:
    talk = Talk()
    und, _ = await talk.say(text)
    assert und.facts["customer_city"] == city and talk.memory["customer_city"] == city


@pytest.mark.parametrize("text", ["Main Office se hoon", "Main Rahul se bol raha hoon", "Delhi bahut door hai", "mere dost Pune mein rehte hain"])
async def test_a_city_is_never_guessed_from_a_bare_word_or_someone_elses_home(text: str) -> None:
    talk = Talk()
    await talk.say(text)
    assert "customer_city" not in talk.memory


async def test_a_changed_city_replaces_the_old_one() -> None:
    talk = Talk({"customer_city": "Jabalpur"})
    await talk.say("Nahi, main Bhopal se hoon.")
    assert talk.memory["customer_city"] == "Bhopal"


async def test_a_stated_budget_is_remembered_in_the_callers_words() -> None:
    talk = Talk()
    await talk.say("Mera budget around 5 lakh hai.")
    assert talk.memory["investment_budget"] == "5 lakh"


# --- 3-4. corrections ------------------------------------------------------------------------------------------------------------------


async def test_a_corrected_budget_silently_replaces_the_old_one() -> None:
    talk = Talk()
    await talk.say("Mera budget 10 lakh hai.")
    und, decision = await talk.say("Nahi, actually 7 lakh.")
    assert talk.memory["investment_budget"] == "7 lakh" and not und.corrections
    assert decision.reply_kind != "confirm_correction"


async def test_an_older_value_does_not_come_back_after_the_correction() -> None:
    talk = Talk()
    await talk.say("Mera budget 10 lakh hai.")
    await talk.say("Nahi, actually 7 lakh.")
    await talk.say("Aap kaise madad karte hain?")
    assert talk.memory["investment_budget"] == "7 lakh"


async def test_budget_changed_again_and_again_always_keeps_the_latest_figure() -> None:
    talk = Talk({"investment_budget": "10 lakh"})
    und, decision = await talk.say("Mera budget 7 lakh hai")
    assert talk.memory["investment_budget"] == "7 lakh" and decision.reply_kind != "clarify_conflict"
    await talk.say("Nahi, 5 lakh tak.")
    assert talk.memory["investment_budget"] == "5 lakh"
    assert "investment_budget_pending" not in talk.memory
    und, decision = await talk.say("Mera budget 6 lakh hai")
    assert talk.memory["investment_budget"] == "6 lakh"
    joined = " ".join(decision.directives)
    assert "Achha, 6 lakh" not in joined  # a changed figure is never echoed back


async def test_a_corrected_day_is_acknowledged_once_and_nothing_is_booked() -> None:
    talk = Talk()
    und, decision = await talk.say("Nahi, Monday.")
    assert dialogue.DATE_CORRECTION in und.signals and decision.action_id == "none"
    assert decision.reply_kind == "confirm_day_correction"
    text = _directives(decision)
    assert "corrected the day or time" in text and "Say sorry once" in text and "Do not book" in text


# --- 5-6. not repeating questions or pressure -------------------------------------------------------------------------------------------


def _plan(talk_memory: dict[str, str], und: Understanding, decision: Decision) -> VoicePlan:
    return VoicePlan(und, decision, dict(talk_memory), None, time.monotonic(), "", ON)


async def test_what_is_already_known_is_handed_to_the_speaking_model_as_do_not_ask_again() -> None:
    talk = Talk()
    await talk.say("Main Jabalpur se hoon.")
    await talk.say("Mera budget 7 lakh hai.")
    und, decision = await talk.say("Aur kya details hain?")
    text = build_instruction(_plan(talk.memory, und, decision), action_result=None)
    assert "do not ask for it again" in text and "customer_city: Jabalpur" in text and "investment_budget: 7 lakh" in text
    assert "meeting_declined" not in text and "ack_last" not in text  # bookkeeping is never shown as a fact


async def test_an_unconfirmed_guess_is_not_presented_as_known() -> None:
    talk = Talk()
    await talk.say("shayad 5 lakh tak laga sakta hoon")
    assert "investment_budget" not in talk.memory and talk.memory["investment_budget_pending"] == "5 lakh"
    und, decision = await talk.say("Aur kya details hain?")
    text = build_instruction(_plan(talk.memory, und, decision), action_result=None)
    assert "investment_budget" not in text
    await talk.say("haan 5 lakh hi hai")
    assert talk.memory["investment_budget"] == "5 lakh"  # confirmed by the caller repeating it


@pytest.mark.parametrize(
    "text",
    ["Mujhe meeting nahi chahiye", "I don't want a meeting", "Aap baar-baar meeting kyun bol rahe ho?", "no need for a meeting"],
)
async def test_a_declined_meeting_is_remembered_and_not_offered_again(text: str) -> None:
    talk = Talk()
    und, decision = await talk.say(text)
    assert dialogue.DECLINES_MEETING in und.signals and talk.memory["meeting_declined"] == "1"
    assert decision.action_id == "none" and decision.push is False
    assert "do not suggest a meeting" in _directives(decision)
    # A later warm message: the agent still must not push.
    _, later = await talk.say("Haan interested hoon, aage bataiye")
    assert later.push is False and later.action_id == "none"
    assert "do not suggest a meeting" in _directives(later)


async def test_turning_down_a_meeting_does_not_mark_the_lead_not_interested() -> None:
    und, decision = await _decide("Mujhe meeting nahi chahiye")
    assert und.scenario == "objection" and decision.action_id == "none"
    assert dialogue.disposition_for(und, decision) != "not_interested"
    # ...but a refusal of everything is still a refusal.
    und2, decision2 = await _decide("Meeting nahi chahiye, interested nahi hoon")
    assert decision2.action_id == "mark_not_interested"


async def test_the_caller_can_still_ask_for_a_meeting_themselves_after_declining() -> None:
    talk = Talk({"meeting_declined": "1"})
    _, decision = await talk.say("Chaliye, kal 5 baje meeting book kar do")
    assert decision.action_id == "book_appointment" and decision.args["time"] == "17:00"


async def test_a_time_that_does_not_work_is_not_a_refusal_of_meetings() -> None:
    talk = Talk()
    und, _ = await talk.say("Meeting nahi ho payega kal")
    assert dialogue.DECLINES_MEETING not in und.signals and "meeting_declined" not in talk.memory


# --- 7-8. hesitation and objections ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Mujhe family se discuss karna hai.", "Ghar par baat karke batata hoon", "I need to discuss it with my wife", "Mujhe sure nahi hai"],
)
async def test_needing_to_talk_it_over_is_hesitation_not_a_no(text: str) -> None:
    talk = Talk()
    und, decision = await talk.say(text)
    assert dialogue.HESITATION in und.signals and decision.reply_kind == "hesitation_support" and decision.push is False
    assert "no hurry" in _directives(decision) and talk.memory["interest_level"] == "undecided"


async def test_not_interested_is_still_an_ending_and_never_a_pitch() -> None:
    _, before = await _decide("Abhi interested nahi hoon.", enhanced=False)
    _, after = await _decide("Abhi interested nahi hoon.")
    assert (after.action_id, after.reply_kind) == (before.action_id, before.reply_kind)
    assert before.action_id == "mark_not_interested"  # a real refusal still ends the pitch exactly as before


async def test_a_profit_question_never_gets_an_invented_number() -> None:
    talk = Talk()
    und, decision = await talk.say("Profit kitna hoga?")
    text = _directives(decision)
    assert "answer it first" in text and "no approved figure" in text and "do not state, estimate" in text
    assert decision.reply_kind == "unknown_followup"  # honest: the team will follow up, nothing is made up


async def test_a_profit_question_with_an_approved_fact_is_answered_from_it() -> None:
    talk = Talk()
    _, decision = await talk.say("Profit kitna hoga?", knowledge=[FACT_INVEST])
    assert decision.reply_kind == "answer" and decision.knowledge and "no approved figure" not in _directives(decision)


async def test_a_whatsapp_request_uses_the_existing_channel_scenario_unchanged() -> None:
    _, before = await _decide("Mujhe WhatsApp par details bhej do.", enhanced=False)
    _, after = await _decide("Mujhe WhatsApp par details bhej do.")
    assert (after.action_id, after.reply_kind) == (before.action_id, before.reply_kind)


# --- 9-10. direct questions and missing knowledge ----------------------------------------------------------------------------------------


async def test_the_company_age_is_its_own_topic_not_a_setup_timeline() -> None:
    assert dialogue.detect_topics("Aapki company kitne saal purani hai?") == ("company",)
    assert dialogue.detect_topics("Setup mein kitne mahine lagenge?") == ("timeline",)


async def test_a_company_question_with_no_approved_answer_is_not_invented() -> None:
    talk = Talk()
    und, decision = await talk.say("Aapki company kitne saal purani hai?")
    assert decision.action_id == "transfer_to_human" and decision.reply_kind == "unknown_followup"
    assert "Unanswered customer question" in decision.args["reason"]
    again = await talk.say("Company kitne saal purani hai batao?")
    assert again[1].reply_kind == "honest_unknown_repeat" and again[1].action_id == "none"  # told honestly, no second handoff


async def test_a_company_question_with_an_approved_answer_is_answered_first() -> None:
    fact = type(FACT_MEETING)(id="k9", title="About us", body="The company has been operating since 2012.")
    _, decision = await Talk().say("Aapki company kitne saal purani hai?", knowledge=[fact])
    assert decision.reply_kind == "answer" and "answer it first" in _directives(decision)


async def test_a_how_does_it_work_question_is_asked_to_be_answered_in_short_segments() -> None:
    _, decision = await Talk().say("Franchise ka process kya hai? kaise shuru karte hain?", knowledge=[FACT_MEETING])
    assert "up to three short sentences" in _directives(decision)


# --- 11-12. acknowledgements, sparingly -------------------------------------------------------------------------------------------------


async def test_a_shared_fact_is_never_echoed_back() -> None:
    talk = Talk()
    for line in ("Mera budget around 5 lakh hai.", "Nahi, 7 lakh tak.", "Main Jabalpur se hoon"):
        _, decision = await talk.say(line)
        text = _directives(decision)
        assert "CRITICAL: Never repeat" in text and "echo it" not in text
        assert not any("Achha, " in d and "lakh" in d for d in decision.directives)


async def test_category_and_industry_changes_are_never_echoed_either() -> None:
    talk = Talk()
    for line in ("Mera budget 10 lakh hai.", "Nahi, 7 lakh.", "Main sports franchise lena chahta hoon", "Actually training academy"):
        _, decision = await talk.say(line)
        assert decision.reply_kind not in ("confirm_correction", "clarify_conflict")
    _, decision = await talk.say("Mera business pharma ka hai")
    assert "CRITICAL: Never repeat" in _directives(decision)


async def test_acknowledgement_guidance_alternates_instead_of_appearing_on_every_turn() -> None:
    talk = Talk()
    flags = []
    for line in ("Mera budget 5 lakh hai", "Aur kya hota hai?", "Main Jabalpur se hoon", "Theek hai, aur batao"):
        _, decision = await talk.say(line)
        flags.append(dialogue._D_ACK_PREFIX in _directives(decision))
    assert flags == [False, True, False, True]  # a turn that carries a detail gets no opener at all


def test_the_acknowledgement_guidance_forbids_fillers_and_repetition() -> None:
    text = dialogue._D_ACK
    assert "Never the same one twice in a row" in text and 'add no "umm"' in text and "many replies need none" in text


# --- 13-17. callbacks, appointments, escalation ---------------------------------------------------------------------------------------


async def test_a_callback_with_a_time_is_still_requested_exactly_as_before() -> None:
    _, before = await _decide("Abhi busy hoon, kal 5 baje call kar lena", enhanced=False)
    _, after = await _decide("Abhi busy hoon, kal 5 baje call kar lena")
    assert after.action_id == "request_callback"
    assert (after.action_id, after.args) == (before.action_id, before.args)


async def test_an_explicit_booking_with_date_and_time_is_made_once() -> None:
    _, decision = await Talk().say("Kal 5 baje meeting book kar do")
    assert decision.action_id == "book_appointment" and decision.args["date"] == "2026-10-16" and decision.args["time"] == "17:00"


async def test_interest_alone_is_not_consent_to_book() -> None:
    _, decision = await Talk().say("Haan interested hoon, aage bataiye")
    assert decision.action_id != "book_appointment"


async def test_a_correction_in_the_same_message_holds_the_booking_for_confirmation() -> None:
    _, decision = await Talk().say("Nahi nahi, kal nahi, parso 5 baje meeting rakh do")
    assert decision.action_id != "book_appointment"


async def test_asking_for_a_human_hands_off_once_and_is_then_answered_honestly() -> None:
    talk = Talk()
    _, first = await talk.say("Mujhe human se baat karni hai")
    assert first.action_id == "transfer_to_human" and talk.memory.get("handoff_requested") == "1"
    _, second = await talk.say("Aapki company kitne saal purani hai?")  # a later unanswerable question
    assert second.action_id == "none" and second.reply_kind == "honest_unknown_repeat"


async def test_a_tool_failure_is_never_reported_as_success() -> None:
    und, decision = await _decide("Kal 5 baje meeting book kar do")
    text = build_instruction(_plan({}, und, decision), action_result={"status": "error"})
    assert "The action did not complete; do not claim it did." in text


# --- 18. endings -----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Thanks, bye", "Theek hai, bye bye", "ab bas, phone rakhta hoon"])
async def test_endings_close_warmly_without_a_pitch(text: str) -> None:
    _, decision = await _decide(text)
    assert decision.push is False and "Close naturally and warmly" in _directives(decision)


# --- 19-21. languages ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "signal"),
    [
        ("मुझे परिवार से बात करनी है, घर पर चर्चा करूँगा", dialogue.HESITATION),
        ("Mujhe ghar par discuss karna hai", dialogue.HESITATION),
        ("I need to discuss this with my family first", dialogue.HESITATION),
        ("Aap baar-baar meeting kyun bol rahe ho?", dialogue.DECLINES_MEETING),
        ("Why do you keep pushing a meeting again and again?", dialogue.DECLINES_MEETING),
    ],
)
async def test_the_same_situations_are_read_in_hindi_hinglish_and_english(text: str, signal: str) -> None:
    und, _ = await Talk().say(text)
    assert signal in und.signals


# --- 23-25. isolation and flag-off ----------------------------------------------------------------------------------------------------


async def test_a_remembered_city_belongs_to_one_org(db_session: Any) -> None:
    import uuid

    org_a, org_b, user = uuid.UUID(ORG_A), uuid.UUID(ORG_B), uuid.uuid4()
    db_session.add_all([Org(id=org_a, name="A"), Org(id=org_b, name="B")])
    await db_session.commit()
    allowed = (*MEMORY_KEYS, *dialogue.ENHANCED_MEMORY_KEYS)
    await save_facts(db_session, org_a, user, {"customer_city": "Jabalpur", "meeting_declined": "1"}, allowed_keys=allowed)
    await save_facts(db_session, org_b, user, {"customer_city": "Indore"}, allowed_keys=allowed)
    await db_session.commit()
    assert await load_memory(db_session, org_a, user) == {"customer_city": "Jabalpur", "meeting_declined": "1"}
    assert await load_memory(db_session, org_b, user) == {"customer_city": "Indore"}  # the decline did not travel


async def test_orgs_that_have_not_opted_in_store_none_of_the_new_facts(db_session: Any) -> None:
    import uuid

    org, user = uuid.UUID(ORG_A), uuid.uuid4()
    db_session.add(Org(id=org, name="A"))
    await db_session.commit()
    written = await save_facts(db_session, org, user, {"customer_city": "Jabalpur", "meeting_declined": "1", "ack_last": "1"})
    assert written == []  # the base whitelist does not include them


@pytest.mark.parametrize(
    "text",
    ["Mujhe meeting nahi chahiye", "Mujhe family se discuss karna hai.", "Nahi, Monday.", "Main Jabalpur se hoon."],
)
async def test_with_the_mode_off_the_new_signals_are_not_attached(text: str) -> None:
    und, decision = await _decide(text, enhanced=False, llm=_llm_says("other", 0.5))
    assert und.signals == frozenset() and decision.directives == ()
    assert "customer_city" not in und.facts


def test_the_misread_name_from_a_hinglish_sentence_is_dropped_only_in_the_enhanced_mode() -> None:
    wrong = Understanding(scenario="busy_callback", intent="busy_callback", emotion="neutral", language="hinglish",
                          confidence=0.9, source="rules", facts={"name": "Baad"})
    kept = dialogue.enhance_understanding(wrong, "Main baad mein call karunga.", memory={}, today=TODAY, language="hinglish")
    assert kept is not None and "name" not in kept.facts
    real = Understanding(scenario="greeting", intent="x", emotion="neutral", language="hinglish", confidence=0.9,
                         source="rules", facts={"name": "Rahul"})
    assert dialogue.enhance_understanding(real, "Main Rahul hoon", memory={}, today=TODAY, language="hinglish").facts["name"] == "Rahul"


async def test_thinking_out_loud_gets_a_no_hold_phrase_directive_and_no_booking() -> None:
    talk = Talk()
    und, decision = await talk.say("Mera budget... ek minute...")
    assert decision.reply_kind == "give_time"
    assert any("Do not use a hold phrase" in d for d in decision.directives)
