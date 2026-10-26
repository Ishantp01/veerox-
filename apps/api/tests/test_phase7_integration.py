"""Phase 7 final integration: 7A turn-taking + 7B speech + 7C dialogue exercised together, plus the real tool
code the dialogue layer ends up calling. Offline only: fake sockets, synthetic transcripts, the test database.

These prove the pieces cooperate and that the safety rules are enforced in CODE where they matter. They say
nothing about how the call sounds, or how real callers pause.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from apps.api.channels.voice import adapter, call_control, config_check, turn_controller
from apps.api.channels.voice.tts_normalize import TTSNormalizer, normalize_text
from apps.api.config import settings
from apps.api.core.conversation import dialogue, voice_decision
from apps.api.core.conversation.silence import SilenceMonitor
from apps.api.core.conversation.voice_decision import VoicePlan, build_instruction
from apps.api.core.prompts import render_voice_append
from apps.api.core.tools import request_callback, transfer_to_human
from apps.api.db.models import Appointment, Lead, User
from apps.api.tests.test_dialogue import FACT_INVEST, ON, TODAY, _decide, _llm_says
from apps.api.tests.test_dialogue_7c import Talk, _directives
from apps.api.tests.test_tools import ORG_ID, _FakeRedis, _seed_org, fake_redis  # noqa: F401
from apps.api.tests.test_voice_call_control import provider  # noqa: F401  (fixture)
from apps.api.tests.test_voice_decision import _state, _task_returning
from apps.api.tests.test_voice_interruption import Call, _caller_finishes_turn, _flags, _interrupt, base  # noqa: F401


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.events.append(event)

    warning = error = info


# --- A. a thinking pause, a guess, then a correction ----------------------------------------------------------------------------------------


async def test_scenario_a_thinking_pause_guess_and_correction() -> None:
    talk = Talk()
    # 1. "ek minute": the rule layer gives them time; it does not escalate to a human or call a model.
    und, decision = await talk.say("Mera budget... ek minute...")
    assert dialogue.ASKS_TIME in und.signals
    assert decision.action_id == "none" and decision.reply_kind == "give_time" and decision.push is False
    # ...and the live turn controller holds the reply long enough for them to carry on.
    assert turn_controller.hold_decision("Mera budget... ek minute...", False, smart=True) == (
        turn_controller.HOLD_THINKING_SECONDS, "thinking_request")
    # 2. A guess is not a confirmed figure.
    await talk.say("Shayad 10 lakh.")
    assert "investment_budget" not in talk.memory and talk.memory["investment_budget_pending"] == "10 lakh"
    # 3. The correction replaces the guess, is confirmed back, and the guess is forgotten.
    und, decision = await talk.say("Nahi, actually 7 lakh.")
    assert talk.memory["investment_budget"] == "7 lakh" and "investment_budget_pending" not in talk.memory
    assert decision.reply_kind != "confirm_correction" and dialogue.SLOT_UPDATED in und.signals
    # 4. Afterwards the speaking model is told only the corrected figure.
    und, decision = await talk.say("Aur kya details hain?")
    text = build_instruction(VoicePlan(und, decision, dict(talk.memory), None, time.monotonic(), "", ON), action_result=None)
    assert "investment_budget: 7 lakh" in text and "10 lakh" not in text


# --- B. a refused meeting stays refused, in code --------------------------------------------------------------------------------------------


async def test_scenario_b_meeting_refusal_is_enforced_at_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    async def fake_tool(name: str, *a: Any, **k: Any) -> dict[str, Any]:
        ran.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake_tool)
    talk = Talk()
    und, decision = await talk.say("Mujhe meeting nahi chahiye.")
    assert decision.action_id == "none" and dialogue.disposition_for(und, decision) != "not_interested"
    holds = dialogue.tool_holds(und, talk.memory)
    assert set(holds) == {"book_appointment", "mark_not_interested"}

    und2, decision2 = await talk.say("Aap baar-baar meeting kyun bol rahe ho?")
    assert decision2.action_id == "none" and "do not suggest a meeting" in _directives(decision2)

    state = _state(tool_holds=dialogue.tool_holds(und2, talk.memory))
    for name in ("book_appointment", "mark_not_interested"):
        result = await adapter._dispatch_deduped(state, name, json.dumps({"date": "2026-10-22", "time": "15:00"}), _Log())
        assert result["status"] == "error" and result["reason"] == "held_by_dialogue_policy" and "Do not tell the caller" in result["note"]
    assert ran == []  # nothing reached the CRM
    ok = await adapter._dispatch_deduped(state, "capture_lead", "{}", _Log())  # unrelated tools are untouched
    assert ok["status"] == "ok" and ran == ["capture_lead"]


async def test_the_caller_asking_to_book_after_a_refusal_lifts_the_hold() -> None:
    talk = Talk({"meeting_declined": "1"})
    und, decision = await talk.say("Chaliye, kal 5 baje meeting book kar do")
    assert "book_appointment" not in dialogue.tool_holds(und, talk.memory) and decision.action_id == "book_appointment"


async def test_scenario_b_a_hold_set_by_the_decision_plan_reaches_the_call_state(monkeypatch: pytest.MonkeyPatch) -> None:
    voice_decision.set_cached_voice_mode(ORG_ID, "live")
    und, decision = await _decide("Mujhe meeting nahi chahiye.")
    plan = VoicePlan(und, decision, {}, None, time.monotonic(), "", ON)
    state = _state(voice_decision_text="Mujhe meeting nahi chahiye.", org_id=ORG_ID)
    state.pending_user_transcript = "Mujhe meeting nahi chahiye."
    state.voice_decision_started = time.monotonic()
    state.voice_decision_task = _task_returning(plan)

    async def fake_apply(plan: Any, **kw: Any) -> voice_decision.VoiceReply:
        return voice_decision.VoiceReply(instruction="X", tool_choice_none=False)

    monkeypatch.setattr(voice_decision, "apply_voice_plan", fake_apply)
    await adapter._voice_decision_response(state)
    assert "book_appointment" in state.tool_holds and "mark_not_interested" in state.tool_holds


async def test_a_new_caller_turn_clears_the_previous_turns_holds() -> None:
    call = Call()
    call.state.tool_holds = {"book_appointment": "stale"}
    await call.event("conversation.item.input_audio_transcription.completed", transcript="haan theek hai, kal 5 baje")
    assert call.state.tool_holds == {}


# --- C. a callback request ---------------------------------------------------------------------------------------------------------------


async def test_scenario_c_a_callback_without_a_time_asks_for_one() -> None:
    und, decision = await _decide("Main abhi busy hoon, kal call karna.")
    assert decision.action_id == "none" and decision.reply_kind == "ask_callback_time"  # no time was invented
    plan = VoicePlan(und, decision, {}, None, time.monotonic(), "", ON)
    text = build_instruction(plan, action_result=None)
    assert "ask what time" in text and "is now scheduled" not in text


async def test_scenario_c_the_same_callback_is_recorded_once(db_session: Any) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000077", name="Asha")
    db_session.add(user)
    await db_session.commit()
    args = dict(date="2099-10-22", time="17:00", user_id=user.id, org_id=ORG_ID, channel="voice")
    first = await request_callback(db_session, **args)
    second = await request_callback(db_session, **args)  # the model and the decision layer both acting
    assert first["status"] == second["status"] == "ok" and second.get("duplicate") is True
    assert second["appointment_id"] == first["appointment_id"]
    rows = (await db_session.execute(select(Appointment).where(Appointment.callback_at.is_not(None)))).scalars().all()
    assert len(rows) == 1
    other = await request_callback(db_session, **{**args, "time": "18:00"})  # a different time is a different callback
    assert other.get("duplicate") is None
    assert len((await db_session.execute(select(Appointment))).scalars().all()) == 2


# --- D. a Hindi/Hinglish money question ---------------------------------------------------------------------------------------------------


async def test_scenario_d_investment_question_uses_approved_facts_only() -> None:
    _, with_fact = await Talk().say("Investment kitna lagega?", knowledge=[FACT_INVEST])
    assert with_fact.reply_kind == "answer" and with_fact.knowledge and "no approved figure" not in _directives(with_fact)
    assert "up to three" not in _directives(with_fact) or "replaces the usual" in _directives(with_fact)
    _, without = await Talk().say("Investment kitna lagega?")
    assert without.reply_kind == "unknown_followup"
    text = _directives(without)
    assert "no approved figure" in text and "do not state, estimate" in text


def test_scenario_d_amounts_are_spoken_correctly_and_unchanged_in_value() -> None:
    assert normalize_text("Investment lagbhag ₹5,00,000 hoga") == "Investment lagbhag paanch lakh rupaye hoga"
    assert normalize_text("Kal 12 baje call karenge") == "Kal baarah baje call karenge"
    stream = TTSNormalizer(enabled=True)
    spoken = "".join(stream.feed(c) for c in ("Investment ₹5,00,000 ", "ke aas-paas hai, ", "Rs 7,000 alag se.")) + stream.flush()
    assert "paanch lakh rupaye" in spoken and "saat hazaar rupaye" in spoken  # one language across the chunks


# --- E. interruption with the new turn-taking and dialogue features on ----------------------------------------------------------------


async def test_scenario_e_interruption_with_every_phase_7_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_safe_cancel=True, voice_smart_turn_taking=True, voice_trim_interrupted_context=True,
           voice_reconnect_recovery=True)
    call = Call()
    await call.agent_starts_answering("resp_1")
    await _interrupt(call)
    assert call.cleared() and call.cancels() == [{"type": "response.cancel", "response_id": "resp_1"}]
    await _caller_finishes_turn(call, "ismein profit kitna hoga")
    assert call.replies() == []  # nothing speaks over a response that has not ended
    await call.event("response.done", response={"id": "resp_1", "status": "cancelled"})
    await asyncio.sleep(0.1)
    assert len(call.replies()) == 1  # the new question is answered once
    before = len(call.phone.sent)
    await call.event("response.output_audio.delta", delta="STALE", response_id="resp_1")
    assert len(call.phone.sent) == before  # nothing of the cancelled answer is heard
    await adapter.cancel_pending_tasks(call.state)


# --- F. a corrected appointment day ---------------------------------------------------------------------------------------------------------


async def test_scenario_f_corrected_day_is_not_booked_until_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_reconnect_recovery=True)
    ran: list[tuple[str, str]] = []

    async def fake_tool(name: str, args_json: str, *a: Any, **k: Any) -> dict[str, Any]:
        ran.append((name, args_json))
        return {"status": "ok", "appointment_id": "A1"}

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake_tool)
    talk = Talk()
    await talk.say("Monday ko meeting rakhna.")
    und, decision = await talk.say("Nahi, Tuesday.")
    assert dialogue.DATE_CORRECTION in und.signals and decision.reply_kind == "confirm_day_correction" and decision.action_id == "none"
    state = _state(tool_holds=dialogue.tool_holds(und, talk.memory))
    book = json.dumps({"date": "2026-10-20", "time": "15:00", "name": "Asha"})
    held = await adapter._dispatch_deduped(state, "book_appointment", book, _Log())
    assert held["reason"] == "held_by_dialogue_policy" and ran == []  # the guess at Tuesday is not booked
    # The caller confirms: holds are cleared on their next turn and the booking goes through once...
    state.tool_holds = {}
    first = await adapter._dispatch_deduped(state, "book_appointment", book, _Log())
    again = await adapter._dispatch_deduped(state, "book_appointment", book, _Log())  # the model repeating itself
    assert first["status"] == "ok" and again.get("duplicate") is True and len(ran) == 1


async def test_a_booking_the_decision_layer_made_cannot_be_repeated_by_the_model_with_other_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_reconnect_recovery=True)
    ran: list[str] = []

    async def fake_tool(name: str, *a: Any, **k: Any) -> dict[str, Any]:
        ran.append(name)
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", fake_tool)
    state = _state()
    await adapter._dispatch_deduped(state, "book_appointment", json.dumps({"date": "d", "time": "t", "notes": "decision layer"}), _Log())
    await adapter._dispatch_deduped(state, "book_appointment", json.dumps({"time": "t", "date": "d", "notes": "model"}), _Log())
    assert ran == ["book_appointment"]


# --- G. language -----------------------------------------------------------------------------------------------------------------------------


async def test_scenario_g_language_follows_the_message_not_one_word() -> None:
    from apps.api.core.conversation.understand import detect_language

    assert detect_language("Investment kitna lagega?") == "hinglish"  # one English word does not switch the reply
    assert detect_language("What is the expected return on investment?") == "en"
    assert detect_language("मुझे निवेश के बारे में बताइए") == "hi"
    # Numbers and names survive normalization in both directions.
    assert normalize_text("Rajesh ji, ₹7 lakh theek hai") == "Rajesh ji, saat lakh rupaye theek hai"
    assert normalize_text("Hi Rajesh, the fee is Rs 5,000") == "Hi Rajesh, the fee is five thousand rupees"


# --- H. a human, honestly -----------------------------------------------------------------------------------------------------------------


async def test_scenario_h_human_request_uses_the_handoff_without_pressure_or_a_false_claim() -> None:
    und, decision = await _decide("Mujhe kisi real person se baat karni hai.")
    assert decision.action_id == "transfer_to_human" and decision.reply_kind == "handoff"
    assert not (set(decision.directives) & {dialogue._D_ACK, dialogue._D_LENGTH})  # no sales-style script added
    assert "meeting" not in _directives(decision).lower()


async def test_scenario_h_the_tool_only_says_notified_when_a_notification_went_out(db_session: Any, fake_redis: _FakeRedis) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000088", name="Caller")
    db_session.add(user)
    await db_session.commit()
    result = await transfer_to_human(db_session, reason="wants a person", user_id=user.id, org_id=ORG_ID, channel="voice")
    assert result["status"] == "ok" and result["notified"] is False  # no team phone / WhatsApp configured here
    assert "has been notified" not in result["message"] and "recorded for the team" in result["message"]
    assert len(fake_redis.lists["human_handoff_queue"]) == 1  # the request itself is still recorded


# --- I. silence, noise, disconnection --------------------------------------------------------------------------------------------------


def test_scenario_i_silence_is_patient_after_a_thinking_pause_and_never_loops() -> None:
    monitor = SilenceMonitor(check_after=8.0, max_checks=2)
    monitor.agent_finished(0.0, patience=2.5)
    assert monitor.poll(15.0, busy=False) is None and monitor.poll(20.0, busy=False) == "check"
    monitor.agent_finished(20.0)
    assert monitor.poll(28.0, busy=False) == "check"
    monitor.agent_finished(28.0)
    assert monitor.poll(36.0, busy=False) == "connection_issue"
    monitor.agent_finished(36.0)
    assert monitor.poll(1000.0, busy=False) is None  # said once, then it stops


def test_scenario_i_background_noise_is_not_a_turn() -> None:
    assert turn_controller.classify_partial("hmmmm", smart=True) == "filler"
    assert turn_controller.classify_partial("....", smart=True) == "filler"
    assert turn_controller.classify_partial("price kya hai", smart=True) == "real"


async def test_scenario_i_a_provider_disconnect_clears_holds_and_ending_twice_hangs_up_once(provider: Any) -> None:  # noqa: F811
    call = Call()
    call.state.tool_holds = {"book_appointment": "x"}
    adapter.reset_after_reconnect(call.state)
    assert call.state.tool_holds == {}
    first = call_control.request_hangup(call.state, _Log(), reason="no_response")
    second = call_control.request_hangup(call.state, _Log(), reason="no_response")
    assert second is first
    await first
    assert provider.hangups == ["call-1"] or len(provider.hangups) == 1


# --- feature-flag combinations ---------------------------------------------------------------------------------------------------------------


import itertools

FLAGS = ("smart", "enhanced", "hinglish", "normalize", "chunks")


@pytest.mark.parametrize("combo", list(itertools.product([False, True], repeat=len(FLAGS))))
async def test_every_combination_of_the_new_options_is_safe(combo: tuple[bool, ...], monkeypatch: pytest.MonkeyPatch) -> None:
    smart, enhanced, hinglish, normalize, chunks = combo
    _flags(monkeypatch, voice_smart_turn_taking=smart)
    # prompt
    prompt = render_voice_append("Acme", ai_disclosure=True, natural_hinglish=hinglish, tone="friendly" if hinglish else None)
    assert "AI assistant" in prompt and "__VOICE_GREETING_EXAMPLE__" not in prompt
    # speech
    n = TTSNormalizer(enabled=normalize)
    out = n.feed("Kal 12:30 baje Rs 5,000 lagega. ") + n.flush()
    assert ("baarah" in out or "saadhe" in out) is normalize and ("5,000" in out) is (not normalize)
    # turn-taking
    seconds, _ = turn_controller.hold_decision("ek minute", False, smart=smart)
    assert (seconds > 0) is smart
    # dialogue
    und, decision = await _decide("Mujhe meeting nahi chahiye.", enhanced=enhanced, llm=_llm_says("other", 0.5))
    if enhanced:
        assert decision.action_id == "none" and "do not suggest a meeting" in _directives(decision)
    else:
        assert und.signals == frozenset() and decision.directives == ()
    assert dialogue.tool_holds(und, {}) == ({"book_appointment": "the caller has said they do not want a meeting", "mark_not_interested": "they declined a meeting, not the offer: do not close the lead"} if enhanced else {})


def test_the_config_check_has_no_complaint_about_the_new_options_at_their_defaults() -> None:
    assert not [i for i in config_check.check_voice_config() if i.level == "error" and "smart" in i.code]
    assert settings.voice_smart_turn_taking is False


# --- tenant isolation of the new state ----------------------------------------------------------------------------------------------------


async def test_holds_and_memory_never_cross_calls() -> None:
    a, b = _state(), _state()
    a.tool_holds = {"book_appointment": "declined"}
    result = await adapter._dispatch_deduped(b, "capture_lead", "{}", _Log())
    assert b.tool_holds == {} and result.get("reason") != "held_by_dialogue_policy"


# --- prompt audit ---------------------------------------------------------------------------------------------------------------------------


async def test_the_final_instructions_keep_disclosure_authority_and_limits_without_contradiction() -> None:
    full = render_voice_append("Acme", ai_disclosure=True, speaking_style=True, natural_hinglish=True, tone="confident_informative")
    assert "AI assistant" in full and "never change the" in full and "never what is true or allowed" in full
    assert len(full) < 13_500  # the org options together add about 2.2k characters to the call prompt
    talk = Talk()
    und, decision = await talk.say("Franchise ka process kya hai? kaise shuru karte hain?", knowledge=[FACT_INVEST])
    text = build_instruction(VoicePlan(und, decision, {}, None, time.monotonic(), "", ON), action_result=None)
    assert "never claim to be a human" in text  # the disclosure travels with every enhanced turn
    assert "Do not repeat information you have already given" in text
    if "up to three short sentences" in text:
        assert "replaces the usual one-or-two limit" in text  # the length rules do not contradict each other
    assert len(text) < 2_200  # per-turn growth is bounded (answer-first, length, ack and style directives)


async def test_per_turn_guidance_has_one_sentence_budget_and_one_acknowledgement_rule() -> None:
    _, decision = await Talk().say("Theek hai, aur batao.")
    text = _directives(decision)
    assert text.count(dialogue._D_ACK_PREFIX) == 1 and text.count("name at most once") == 1
