"""Phase 4: the enhanced dialogue policy (core/conversation/dialogue.py) and how
it plugs into understand / decide, without a database or a model.

Every conversation fragment below is synthetic. The enhanced mode is an org
opt-in (EngineConfig.enhanced_dialogue); the first group of tests proves that
with it OFF nothing changes.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from apps.api.core.conversation import dialogue
from apps.api.core.conversation.config import default_config
from apps.api.core.conversation.decide import decide
from apps.api.core.conversation.dialogue import DialogueState
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.understand import UnderstandError, understand
from apps.api.core.llm import ChatResult

TODAY = date(2026, 10, 15)  # a Thursday
TOMORROW = "2026-10-16"
DAY_AFTER = "2026-10-17"

OFF = default_config()
ON = replace(default_config(), enhanced_dialogue=True)

FACT_MEETING = SimpleNamespace(
    id="k1", title="First meeting", body="The first meeting is informational. It does not commit you to anything."
)
FACT_INVEST = SimpleNamespace(
    id="k2", title="Investment", body="Investment depends on the store format; the franchise team shares it after a first conversation."
)


class _CountingLLM:
    """A model stand-in that records how often it is called."""

    def __init__(self, content: str | None = None) -> None:
        self.calls = 0
        self._content = content

    async def __call__(self, messages: list[dict[str, Any]]) -> ChatResult:
        self.calls += 1
        if self._content is None:
            raise RuntimeError("the model must not be called here")
        return ChatResult(content=self._content, tokens_in=1, tokens_out=1)


def _llm_says(scenario: str, confidence: float = 0.9) -> _CountingLLM:
    return _CountingLLM(json.dumps({"scenario": scenario, "intent": "x", "emotion": "neutral",
                                    "confidence": confidence, "facts": {}, "action_id": "none"}))


async def _understand(text: str, *, enhanced: bool = True, memory: dict[str, str] | None = None,
                      state: str | None = None, llm: _CountingLLM | None = None) -> Understanding:
    return await understand(
        text, state=state, history=[], memory=memory or {}, llm=llm or _CountingLLM(),
        timeout=1.0, today=TODAY, config=ON if enhanced else OFF,
    )


async def _decide(text: str, *, memory: dict[str, str] | None = None, knowledge: list[Any] | None = None,
                  state: str | None = None, llm: _CountingLLM | None = None, enhanced: bool = True,
                  channel: str = "voice") -> tuple[Understanding, Decision]:
    memory = memory or {}
    und = await _understand(text, enhanced=enhanced, memory=memory, state=state, llm=llm)
    decision = decide(und, text=text, memory=memory, knowledge=knowledge or [], channel=channel,
                      config=ON if enhanced else OFF)
    return und, decision


# --- the mode is strictly opt-in ---------------------------------------------------------------------------------


async def test_with_the_mode_off_nothing_is_attached_and_the_old_path_runs() -> None:
    llm = _llm_says("objection")
    und, decision = await _decide("Mujhe abhi sure nahi hai.", enhanced=False, llm=llm)
    assert llm.calls == 1  # the base rules could not place it: the model was asked, as before
    assert und.signals == frozenset() and und.corrections == {} and und.conflicts == {}
    assert decision.directives == () and decision.push is True
    assert decision.reply_kind == "objection_followup"  # the old nudge toward a callback


async def test_with_the_mode_on_the_same_message_needs_no_model_call() -> None:
    llm = _CountingLLM()
    und, decision = await _decide("Mujhe abhi sure nahi hai.", llm=llm)
    assert llm.calls == 0  # one fewer LLM call than before
    assert dialogue.HESITATION in und.signals and und.facts["interest_level"] == "undecided"
    assert decision.reply_kind == "hesitation_support" and decision.push is False


@pytest.mark.parametrize(
    "text",
    ["I am very angry, this is a scam", "Please connect me to your manager", "Not interested, don't call me",
     "Thanks, bye"],
)
async def test_escalations_and_endings_are_decided_exactly_as_before(text: str) -> None:
    _, before = await _decide(text, enhanced=False)
    _, after = await _decide(text)
    assert (after.action_id, after.args, after.reply_kind) == (before.action_id, before.args, before.reply_kind)
    assert after.action_id == before.action_id


async def test_a_business_defined_scenario_keeps_its_own_logic() -> None:
    from apps.api.core.conversation.config import ScenarioDef

    custom = ScenarioDef(id="my_scenario", name="Mine", goal="g", actions=("none",), examples=("zebra",))
    cfg = replace(default_config(), enhanced_dialogue=True,
                  scenarios={**default_config().scenarios, "my_scenario": custom})
    und = Understanding("my_scenario", "x", "neutral", "en", 0.9, "rules", signals=frozenset({dialogue.HESITATION}))
    decision = decide(und, text="zebra, not sure", memory={}, knowledge=[], config=cfg)
    assert decision.reply_kind == "guided" and decision.directives == ()


# --- what each kind of message becomes ---------------------------------------------------------------------------


CASES = [
    # (text, expected reply kind, signal that must be present)
    ("Aap kaun bol rahe ho?", "identify_self", dialogue.ASKS_IDENTITY),
    ("Mujhe abhi sure nahi hai.", "hesitation_support", dialogue.HESITATION),
    ("Aap log baad mein pressure to nahi doge na?", "reassure_no_pressure", dialogue.PRESSURE),
    ("Mujhe online meeting prefer hai.", "online_meeting_ok", dialogue.PREFERS_ONLINE),
    ("Kal travel karunga.", "flexible_followup", dialogue.UNAVAILABLE),
    ("Us time par nahi ho payega.", "flexible_followup", dialogue.DECLINES_TIME),
    ("Aap baar-baar wahi baat kyun pooch rahe hain?", "repetition_apology", dialogue.REPETITION),
]


@pytest.mark.parametrize(("text", "kind", "signal"), CASES)
async def test_the_situation_picks_the_reply(text: str, kind: str, signal: str) -> None:
    llm = _CountingLLM()
    und, decision = await _decide(text, llm=llm)
    assert signal in und.signals
    assert decision.reply_kind == kind
    assert decision.action_id == "none"  # none of these runs a tool
    assert llm.calls == 0, "settled by rules: no model call, no added latency"


async def test_end_of_call_requests_are_recognised() -> None:
    und, decision = await _decide("Theek hai, phone rakhta hoon.")
    assert und.scenario == "closing" and dialogue.END_CALL in und.signals
    assert decision.reply_kind == "closing" and decision.push is False
    assert any("Close naturally" in d for d in decision.directives)


async def test_hesitation_is_not_a_price_objection_and_never_pushes() -> None:
    _, decision = await _decide("Mujhe abhi sure nahi hai.", state="awaiting_callback_time")
    assert decision.reply_kind == "hesitation_support"
    assert any("Do not ask them to book" in d for d in decision.directives)


# --- direct questions come first, and are never answered with an invented figure -------------------------------


async def test_a_direct_question_is_answered_first_from_approved_facts() -> None:
    und, decision = await _decide("Sochna padega, investment kitna lagega?", knowledge=[FACT_INVEST])
    assert {dialogue.HESITATION, dialogue.DIRECT_QUESTION, "topic:investment"} <= und.signals
    assert decision.reply_kind == "answer" and decision.knowledge == [FACT_INVEST]
    assert decision.directives[0].startswith("The caller asked a direct question")
    assert any("still deciding" in d for d in decision.directives)  # then the hesitation is acknowledged
    assert decision.push is False


async def test_without_approved_facts_profit_is_not_answered_and_figures_are_forbidden() -> None:
    und, decision = await _decide("Profit kitna hoga?")
    assert decision.action_id == "transfer_to_human" and decision.reply_kind == "unknown_followup"
    assert "Profit" in decision.args["reason"]
    assert any("do not state, estimate or hint at any number" in d for d in decision.directives)
    assert decision.next_state == "engaged"  # the conversation carries on after the follow-up is raised


async def test_the_same_unanswerable_question_does_not_raise_a_second_handoff() -> None:
    memory = {"handoff_requested": "1", "answered_topics": "profit"}
    _, decision = await _decide("Aur profit kitna hoga?", memory=memory)
    assert decision.action_id == "none" and decision.reply_kind == "honest_unknown_repeat"
    assert any("no approved figure" in d or "do not state" in d for d in decision.directives)


async def test_a_topic_already_answered_is_not_explained_again() -> None:
    memory = {"answered_topics": "investment"}
    _, decision = await _decide("Investment kitna lagega?", memory=memory, knowledge=[FACT_INVEST])
    assert decision.reply_kind == "answer"
    assert any("already given this earlier" in d for d in decision.directives)


async def test_commitment_question_uses_approved_facts_not_a_promise() -> None:
    text = "Meeting karne ke baad koi commitment toh nahi hoga?"
    _, with_fact = await _decide(text, knowledge=[FACT_MEETING])
    assert with_fact.reply_kind == "answer" and with_fact.knowledge == [FACT_MEETING]
    _, without = await _decide(text)
    assert without.reply_kind == "unknown_followup"  # nothing approved: the team confirms
    joined = " ".join(without.directives)
    assert "Never promise there is no obligation" in joined and "never state fees" in joined


# --- corrections, conflicts, verified facts --------------------------------------------------------------------------


async def test_an_explicit_correction_silently_replaces_the_fact() -> None:
    memory = {"property_info": "Meri dukaan 500 sq ft ki hai"}
    und, decision = await _decide("Nahi nahi, dukaan 800 sq ft ki hai, 500 galat tha.", memory=memory)
    assert und.facts["property_info"] == "dukaan 800 sq ft ki hai"
    assert dialogue.SLOT_UPDATED in und.signals and dialogue.CORRECTION not in und.signals
    assert not und.corrections and not und.conflicts
    assert decision.reply_kind not in ("confirm_correction", "clarify_conflict")
    assert any("Quietly use the new value" in d for d in decision.directives)


async def test_a_differing_mention_without_a_correction_overwrites_without_asking() -> None:
    memory = {"property_info": "Meri dukaan 500 sq ft ki hai"}
    und, decision = await _decide("Meri dukaan 800 sq ft ki hai.", memory=memory)
    assert dialogue.CONFLICT not in und.signals and not und.conflicts
    assert und.facts["property_info"] == "Meri dukaan 800 sq ft ki hai"
    assert "property_info_pending" not in und.facts
    assert decision.reply_kind != "clarify_conflict"
    updates, forget = dialogue.memory_updates(und, decision, DialogueState.from_memory(memory), memory)
    assert updates["property_info"].endswith("800 sq ft ki hai") and forget == []


async def test_a_hedged_new_value_is_still_only_a_pending_guess() -> None:
    memory = {"property_info": "Meri dukaan 500 sq ft ki hai"}
    und, _ = await _decide("Shayad meri dukaan 800 sq ft ki hai.", memory=memory)
    assert "property_info" not in und.facts and dialogue.SLOT_UPDATED not in und.signals
    assert und.facts["property_info_pending"].endswith("800 sq ft ki hai")


async def test_saying_the_new_value_again_replaces_it_and_clears_the_guess() -> None:
    memory = {
        "property_info": "Meri dukaan 500 sq ft ki hai",
        "property_info_pending": "Meri dukaan 800 sq ft ki hai",
    }
    und, decision = await _decide("Meri dukaan 800 sq ft ki hai.", memory=memory)
    assert und.facts["property_info"] == "Meri dukaan 800 sq ft ki hai"
    updates, forget = dialogue.memory_updates(und, decision, DialogueState.from_memory(memory), memory)
    assert updates["property_info"].endswith("800 sq ft ki hai") and forget == ["property_info_pending"]


async def test_a_first_statement_is_stored_and_the_same_value_is_not_a_conflict() -> None:
    und, _ = await _decide("Meri dukaan 500 sq ft ki hai.")
    assert und.facts["property_info"] == "Meri dukaan 500 sq ft ki hai" and not und.conflicts
    memory = {"property_info": "Meri dukaan 500 sq ft ki hai"}
    again, _ = await _decide("meri dukaan 500 sq ft ki hai", memory=memory)
    assert dialogue.CONFLICT not in again.signals


async def test_a_stray_name_does_not_overwrite_the_verified_one() -> None:
    memory = {"name": "Amit"}
    und, _ = await _decide("Main Rahul hoon, interested hoon.", memory=memory)
    assert "name" not in und.facts  # one differing mention is not a correction
    corrected, _ = await _decide("Nahi nahi, mera naam Rahul hai.", memory=memory, llm=_llm_says("identification"))
    assert corrected.facts.get("name") == "Rahul"


async def test_budget_and_background_are_remembered_as_the_customers_own_words() -> None:
    und, _ = await _decide("Mera investment budget 15 lakh tak hai.", llm=_llm_says("discovery"))
    assert und.facts["investment_budget"].lower().startswith("15 lakh")
    background, decision = await _decide("Mera pharma ka koi background nahi hai.", llm=_llm_says("discovery"))
    assert "background" in background.facts["customer_background"]
    assert any("no prior background" in d for d in decision.directives)


# --- scheduling ----------------------------------------------------------------------------------------------------------


async def test_tomorrow_is_not_scheduled_when_the_customer_is_travelling() -> None:
    und, decision = await _decide("Kal travel karunga.")
    assert "callback_date" not in und.facts and und.facts["unavailable_dates"] == TOMORROW
    assert decision.action_id == "none" and decision.reply_kind == "flexible_followup"
    assert any("do not book or schedule" in d for d in decision.directives)


async def test_a_usable_alternative_in_the_same_breath_is_honoured() -> None:
    und, decision = await _decide("Kal travel karunga, parso 6 baje call karna.")
    assert und.facts["unavailable_dates"] == TOMORROW
    assert und.facts["callback_date"] == DAY_AFTER and und.facts["callback_time_hhmm"] == "18:00"
    assert decision.action_id == "request_callback"
    assert (decision.args["date"], decision.args["time"]) == (DAY_AFTER, "18:00")


async def test_a_declined_slot_is_never_booked_and_the_alternative_is_kept() -> None:
    und, decision = await _decide("Kal 5 baje nahi ho payega, parso 6 baje rakhiye.")
    assert dialogue.DECLINES_TIME in und.signals
    assert und.facts["callback_date"] == DAY_AFTER  # the declined day was dropped, the offered one kept
    assert decision.action_id != "book_appointment"


async def test_a_callback_without_a_time_asks_for_one_instead_of_guessing() -> None:
    text = "Kal call kar lena."
    _, base = await _decide(text, enhanced=False)
    assert base.action_id == "request_callback" and base.args["time"] == "11:00"  # the old default time
    _, improved = await _decide(text)
    assert improved.action_id == "none" and improved.reply_kind == "ask_callback_time"


async def test_a_callback_with_a_stated_time_is_unchanged() -> None:
    _, base = await _decide("Please call me tomorrow at 5 pm", enhanced=False)
    _, improved = await _decide("Please call me tomorrow at 5 pm")
    assert improved.action_id == base.action_id == "request_callback"
    assert improved.args["time"] == base.args["time"] == "17:00"


async def test_an_explicit_booking_with_date_and_time_goes_through_with_the_method_noted() -> None:
    text = "Meeting book kar do parso shaam 5 baje"
    und, decision = await _decide(text, memory={"meeting_preference": "online"})
    assert und.scenario == "appointment_request"
    assert decision.action_id == "book_appointment"
    assert (decision.args["date"], decision.args["time"]) == (DAY_AFTER, "17:00")
    assert decision.args["notes"].endswith("Meeting method: online")


@pytest.mark.parametrize(
    "text",
    [
        "Meeting book kar do parso 5 baje, par abhi sure nahi hai",   # hesitating
        "Meeting book kar do parso 5 baje, pressure mat daalna",      # worried about pressure
        "Meeting book kar do kal 5 baje, kal travel karunga",         # contradicts itself
    ],
)
async def test_a_hesitant_customer_is_not_booked(text: str) -> None:
    _, decision = await _decide(text)
    assert decision.action_id == "none"  # nothing is booked
    assert decision.reply_kind in (
        "hesitation_support", "reassure_no_pressure", "soft_next_step", "flexible_followup"
    )


async def test_a_booking_request_with_no_time_asks_rather_than_books() -> None:
    _, decision = await _decide("Meeting book kar do")
    assert decision.action_id == "none" and decision.reply_kind == "ask_appointment_time"


# --- not pushing -----------------------------------------------------------------------------------------------------------


async def test_a_warm_customer_is_nudged_once_then_left_alone() -> None:
    text = "Haan interested hoon, aur details batao"
    und, first = await _decide(text)
    assert und.scenario == "interested"
    assert first.reply_kind in dialogue.PUSH_KINDS and first.push is True
    updates, _ = dialogue.memory_updates(und, first, DialogueState(), {})
    assert updates["appointment_asks"] == "1"
    _, second = await _decide(text, memory={"appointment_asks": "1"})
    assert second.reply_kind == "soft_next_step" and second.push is False


async def test_an_undecided_customer_is_never_nudged() -> None:
    _, decision = await _decide("Haan interested hoon", memory={"interest_level": "undecided"})
    assert decision.reply_kind == "soft_next_step"
    _, pressured = await _decide("Haan interested hoon", memory={"pressure_concern": "1"})
    assert pressured.reply_kind == "soft_next_step"


async def test_appointment_readiness_rules() -> None:
    warm = Understanding("interested", "x", "positive", "en", 0.9, "rules")
    assert dialogue.appointment_ready(warm, DialogueState(), {})
    assert not dialogue.appointment_ready(warm, DialogueState(appointment_asks=1), {})
    assert not dialogue.appointment_ready(replace(warm, signals=frozenset({dialogue.HESITATION})), DialogueState(), {})
    assert not dialogue.appointment_ready(replace(warm, signals=frozenset({dialogue.UNAVAILABLE})), DialogueState(), {})
    assert not dialogue.appointment_ready(Understanding("price_question", "x", "neutral", "en", 0.9, "rules"),
                                          DialogueState(), {})


# --- conversation style ----------------------------------------------------------------------------------------------------------


async def test_every_enhanced_turn_carries_the_style_rules_and_never_claims_to_be_human() -> None:
    _, decision = await _decide("Mujhe abhi sure nahi hai.")
    style = [d for d in decision.directives if d.startswith("Keep it short")]
    assert len(style) == 1
    assert "name at most once" in style[0] and "never claim to be a human employee" in style[0]
    assert len(set(decision.directives)) == len(decision.directives)  # no duplicated instruction


# --- what is remembered ------------------------------------------------------------------------------------------------------------


async def test_memory_updates_track_answers_handoffs_and_pressure() -> None:
    und, decision = await _decide("Profit kitna hoga? Pressure mat daalna.")
    updates, _ = dialogue.memory_updates(und, decision, DialogueState(), {})
    assert updates["handoff_requested"] == "1" and updates["answered_topics"] == "profit"
    assert updates["pressure_concern"] == "1"
    assert set(updates) <= set(dialogue.ENHANCED_MEMORY_KEYS)  # nothing outside the enhanced whitelist


async def test_the_name_is_used_once() -> None:
    und = Understanding("greeting", "g", "neutral", "en", 0.9, "rules", facts={"name": "Asha"})
    updates, _ = dialogue.memory_updates(und, Decision("none", {}, "greeting"), DialogueState(), {"name": "Asha"})
    assert updates["name_used"] == "1"
    from apps.api.core.conversation.reply import build_reply

    first, _, _ = await build_reply(Decision("none", {}, "greeting"), und, text="hi", llm=None, timeout=1.0,
                                    memory={"name": "Asha"})
    later, _, _ = await build_reply(Decision("none", {}, "greeting"), und, text="hi", llm=None, timeout=1.0,
                                    memory={"name": "Asha", "name_used": "1"})
    assert "Asha" in first and "Asha" not in later


# --- CRM disposition (existing Lead.tags, no new column) ----------------------------------------------------------------------------


def _d(kind: str = "answer", action: str = "none", scenario: str = "interested", signals: frozenset[str] = frozenset(),
       **facts: str) -> tuple[Understanding, Decision]:
    return Understanding(scenario, "x", "neutral", "en", 0.9, "rules", facts=dict(facts), signals=signals), Decision(
        action, {}, kind
    )


def test_dispositions_distinguish_the_six_states() -> None:
    ok = {"status": "ok"}
    assert dialogue.disposition_for(*_d("callback_confirmed", "request_callback"), ok) == "callback_requested"
    assert dialogue.disposition_for(*_d("appointment_confirmed", "book_appointment"), ok) == "meeting_confirmed"
    assert dialogue.disposition_for(*_d("interested"), None) == "meeting_proposed"
    assert dialogue.disposition_for(*_d("not_interested", "mark_not_interested", "not_interested"), ok) == "not_interested"
    assert dialogue.disposition_for(*_d("hesitation_support", signals=frozenset({dialogue.HESITATION}))) == "undecided"
    assert dialogue.disposition_for(*_d("answer", scenario="interested")) == "interested"
    assert dialogue.disposition_for(*_d("answer", scenario="price_question")) is None


def test_a_booking_the_tool_did_not_confirm_is_only_proposed() -> None:
    failed = {"status": "error", "reason": "slot_conflict"}
    assert dialogue.disposition_for(*_d("appointment_confirmed", "book_appointment"), failed) == "meeting_proposed"
    assert dialogue.disposition_for(*_d("callback_confirmed", "request_callback"), failed) is None


def test_an_undecided_customer_is_never_recorded_as_confirmed() -> None:
    und, decision = _d("hesitation_support", signals=frozenset({dialogue.HESITATION, dialogue.PREFERS_ONLINE}),
                       interest_level="undecided")
    assert dialogue.disposition_for(und, decision, None) == "undecided"


def test_tags_hold_one_disposition_and_a_weak_signal_never_downgrades_a_real_outcome() -> None:
    tags = dialogue.apply_disposition_to_tags(["vip"], "interested")
    assert tags == ["vip", "disposition:interested"]
    assert dialogue.apply_disposition_to_tags(tags, "interested") is None  # unchanged: nothing to write
    tags = dialogue.apply_disposition_to_tags(tags, "undecided")
    assert tags == ["vip", "disposition:undecided"]  # the customer's latest stance wins between the soft two
    tags = dialogue.apply_disposition_to_tags(tags, "meeting_confirmed")
    assert tags == ["vip", "disposition:meeting_confirmed"]
    assert dialogue.apply_disposition_to_tags(tags, "undecided") is None  # a booked meeting is not downgraded
    assert dialogue.apply_disposition_to_tags(tags, "meeting_proposed") is None
    assert dialogue.apply_disposition_to_tags(tags, "not_interested") == ["vip", "disposition:not_interested"]
    assert dialogue.apply_disposition_to_tags(None, None) is None


# --- model failures never break the policy ---------------------------------------------------------------------------------------------


async def test_a_model_failure_still_falls_back_exactly_as_before() -> None:
    with pytest.raises(UnderstandError):
        await _understand("zebra quantum marmalade", llm=_CountingLLM())  # the model is asked and fails


def test_hinglish_not_sure_if_interested_is_hesitation_and_cushioned() -> None:
    from datetime import date

    text = "mujhe samajh nahi aa raha hai ki interested hoon ya nahi"
    assert dialogue.HESITATION in dialogue.analyse(text, memory={}, today=date(2026, 10, 23)).signals
    assert "MUST open with one soft sentence" in dialogue._D_ACK_HESITATION
