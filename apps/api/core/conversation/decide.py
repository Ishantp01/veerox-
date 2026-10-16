"""Process 4.0 — decide the next action under business policy.

Pure policy: no I/O, no LLM. Given what the customer meant, what we
remember and what approved knowledge matched, pick exactly one approved
action id plus the reply kind. The model never gets to run an action — it
may only *propose* one, and ``validate_action`` rejects anything off the
allow-list or with missing required arguments.
"""

from __future__ import annotations

from typing import Any

from apps.api.core.conversation.config import EngineConfig, ScenarioDef
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    STATE_APPOINTMENT_SET,
    STATE_AWAITING_APPOINTMENT_TIME,
    STATE_AWAITING_CALLBACK_TIME,
    STATE_CALLBACK_SET,
    STATE_CLOSED,
    STATE_DISCOVERY,
    STATE_ENGAGED,
    STATE_HANDED_OFF,
    STATE_INFO_SENT,
    STATE_OBJECTION,
    STATE_QUALIFICATION,
    Decision,
    Understanding,
)
from apps.api.core.tools import TOOL_DEFINITIONS

_DEFAULT_CALLBACK_TIME = "11:00"
_MAX_REASON_LEN = 300


def _required_args(action_id: str) -> list[str]:
    for definition in TOOL_DEFINITIONS:
        function = definition.get("function", {})
        if function.get("name") == action_id:
            return list(function.get("parameters", {}).get("required", []))
    return []


def validate_action(action_id: str, args: dict[str, Any]) -> bool:
    """True only for an allow-listed action whose required args are all present."""
    if action_id not in ALLOWED_ACTIONS:
        return False
    if action_id == "none":
        return True
    return all(args.get(key) not in (None, "") for key in _required_args(action_id))


def _resolve_callback(facts: dict[str, str], memory: dict[str, str]) -> tuple[str, str] | None:
    """(date, HH:MM) for a callback, from this turn's facts or remembered callback_time."""
    date_value = facts.get("callback_date")
    time_value = facts.get("callback_time_hhmm")
    if date_value and time_value:
        return date_value, time_value
    if date_value:
        return date_value, _DEFAULT_CALLBACK_TIME
    remembered = memory.get("callback_time")
    if remembered and " " in remembered:
        remembered_date, remembered_time = remembered.split(" ", 1)
        return remembered_date, remembered_time
    return None


# Reply kinds that tell the customer an action happened. When a scenario does
# not permit that action the reply must not claim it.
_ACTION_CLAIMING_KINDS = frozenset(
    {
        "callback_confirmed",
        "appointment_confirmed",
        "whatsapp_sent",
        "handoff",
        "angry_handoff",
        "not_interested",
    }
)


def apply_scenario_actions(decision: Decision, scenario: ScenarioDef | None) -> Decision:
    """Enforce the scenario's own list of allowed actions (Scenario Builder).

    An action the scenario does not allow is dropped, and a reply that would
    have announced it becomes a guided reply instead. A scenario can only ever
    narrow what the engine does: it cannot add an action the rules did not pick.
    """
    if scenario is None or decision.action_id == "none" or decision.action_id in scenario.actions:
        return decision
    kind = "guided" if decision.reply_kind in _ACTION_CLAIMING_KINDS else decision.reply_kind
    return Decision("none", {}, kind, knowledge=decision.knowledge, next_state=STATE_ENGAGED)


def _custom_decision(
    scenario: ScenarioDef,
    understanding: Understanding,
    *,
    reason: str,
    memory: dict[str, str],
    knowledge: list[Any],
) -> Decision:
    """A business-defined scenario: run its one configured action when the
    inputs for it are there, otherwise just give a guided reply."""
    action = next((a for a in scenario.actions if a != "none" and a in ALLOWED_ACTIONS), "none")
    facts = understanding.facts
    name = facts.get("name") or memory.get("name")
    if action == "request_callback":
        resolved = _resolve_callback(facts, memory)
        if resolved is None:
            return Decision("none", {}, "ask_callback_time", next_state=STATE_AWAITING_CALLBACK_TIME)
        args: dict[str, Any] = {
            "date": resolved[0],
            "time": resolved[1],
            "notes": f"{scenario.name}: {reason}",
        }
        if name:
            args["name"] = name
        return Decision(action, args, "callback_confirmed", next_state=STATE_CALLBACK_SET)
    if action == "book_appointment":
        date_value, time_value = facts.get("callback_date"), facts.get("callback_time_hhmm")
        if not (date_value and time_value):
            return Decision(
                "none", {}, "ask_appointment_time", next_state=STATE_AWAITING_APPOINTMENT_TIME
            )
        args = {"date": date_value, "time": time_value, "notes": f"{scenario.name}: {reason}"}
        if name:
            args["name"] = name
        return Decision(action, args, "appointment_confirmed", next_state=STATE_APPOINTMENT_SET)
    if action == "transfer_to_human":
        return Decision(
            action,
            {"reason": f"{scenario.name}: {reason}", "urgency": "medium"},
            "handoff",
            next_state=STATE_HANDED_OFF,
        )
    if action == "mark_not_interested":
        return Decision(action, {"reason": reason}, "not_interested", next_state=STATE_CLOSED)
    if action == "send_whatsapp_message" and knowledge:
        message = "\n\n".join(f"{item.title}: {item.body}" for item in knowledge)
        return Decision(
            action,
            {"message": message},
            "whatsapp_sent",
            knowledge=knowledge,
            next_state=STATE_INFO_SENT,
        )
    return Decision("none", {}, "guided", knowledge=knowledge, next_state=STATE_ENGAGED)


def decide(
    understanding: Understanding,
    *,
    text: str,
    memory: dict[str, str],
    knowledge: list[Any],
    channel: str = "whatsapp",
    config: EngineConfig | None = None,
) -> Decision:
    decision = _decide(
        understanding, text=text, memory=memory, knowledge=knowledge, channel=channel, config=config
    )
    if config is not None:
        decision = apply_scenario_actions(decision, config.get(understanding.scenario))
    return decision


def _decide(
    understanding: Understanding,
    *,
    text: str,
    memory: dict[str, str],
    knowledge: list[Any],
    channel: str,
    config: EngineConfig | None,
) -> Decision:
    scenario = understanding.scenario
    reason = text.strip()[:_MAX_REASON_LEN]

    if config is not None:
        defined = config.get(scenario)
        if defined is not None and not defined.builtin:
            return _custom_decision(
                defined, understanding, reason=reason, memory=memory, knowledge=knowledge
            )

    if scenario == "human_handoff":
        return Decision(
            "transfer_to_human",
            {"reason": f"Customer asked for a human: {reason}", "urgency": "medium"},
            "handoff",
            next_state=STATE_HANDED_OFF,
        )
    if scenario == "angry":
        return Decision(
            "transfer_to_human",
            {"reason": f"Customer is upset: {reason}", "urgency": "high"},
            "angry_handoff",
            next_state=STATE_HANDED_OFF,
        )
    if scenario == "not_interested":
        return Decision(
            "mark_not_interested", {"reason": reason}, "not_interested", next_state=STATE_CLOSED
        )
    if scenario == "closing":
        return Decision("none", {}, "closing", next_state=STATE_CLOSED)
    if scenario == "identification":
        return Decision("none", {}, "identification", next_state=STATE_DISCOVERY)
    if scenario == "discovery":
        known_business = understanding.facts.get("business_type") or memory.get("business_type")
        known_pain = understanding.facts.get("pain_point") or memory.get("pain_point")
        if not known_business:
            return Decision("none", {}, "discovery_business", next_state=STATE_DISCOVERY)
        if not known_pain:
            return Decision("none", {}, "discovery_pain", next_state=STATE_QUALIFICATION)
        return Decision("none", {}, "discovery_offer", next_state=STATE_AWAITING_CALLBACK_TIME)
    if scenario == "discount_request":
        if knowledge:
            return Decision("none", {}, "answer", knowledge=knowledge, next_state=STATE_OBJECTION)
        # No approved offer on file: never promise one.
        return Decision("none", {}, "discount_followup", next_state=STATE_AWAITING_CALLBACK_TIME)
    if scenario in ("busy_callback", "callback_request"):
        resolved = _resolve_callback(understanding.facts, memory)
        if resolved is None:
            return Decision(
                "none", {}, "ask_callback_time", next_state=STATE_AWAITING_CALLBACK_TIME
            )
        callback_date, callback_time = resolved
        args: dict[str, Any] = {
            "date": callback_date,
            "time": callback_time,
            "notes": f"Customer asked for a callback: {reason}",
        }
        name = understanding.facts.get("name") or memory.get("name")
        if name:
            args["name"] = name
        return Decision("request_callback", args, "callback_confirmed", next_state=STATE_CALLBACK_SET)

    if scenario == "appointment_request":
        date_value = understanding.facts.get("callback_date")
        time_value = understanding.facts.get("callback_time_hhmm")
        if not (date_value and time_value):
            return Decision(
                "none", {}, "ask_appointment_time", next_state=STATE_AWAITING_APPOINTMENT_TIME
            )
        appointment_args: dict[str, Any] = {
            "date": date_value,
            "time": time_value,
            "notes": f"Customer asked for an appointment: {reason}",
        }
        appointment_name = understanding.facts.get("name") or memory.get("name")
        if appointment_name:
            appointment_args["name"] = appointment_name
        return Decision(
            "book_appointment",
            appointment_args,
            "appointment_confirmed",
            next_state=STATE_APPOINTMENT_SET,
        )

    if scenario == "whatsapp_info_request":
        if not knowledge:
            # Nothing approved to send: never send a made-up message.
            return Decision(
                "transfer_to_human",
                {"reason": f"Customer asked for details on WhatsApp: {reason}", "urgency": "low"},
                "unknown_followup",
                next_state=STATE_HANDED_OFF,
            )
        if channel == "whatsapp":
            # Already on WhatsApp: just answer here from the approved text.
            return Decision("none", {}, "answer", knowledge=knowledge, next_state=STATE_ENGAGED)
        message = "\n\n".join(f"{item.title}: {item.body}" for item in knowledge)
        return Decision(
            "send_whatsapp_message",
            {"message": message},
            "whatsapp_sent",
            knowledge=knowledge,
            next_state=STATE_INFO_SENT,
        )

    if scenario == "greeting":
        return Decision("none", {}, "greeting", next_state=STATE_ENGAGED)

    # price_question / objection / interested / confused / unknown_question
    if knowledge:
        next_state = (
            STATE_AWAITING_CALLBACK_TIME if scenario == "interested" else STATE_ENGAGED
        )
        return Decision("none", {}, "answer", knowledge=knowledge, next_state=next_state)
    if scenario == "interested":
        return Decision("none", {}, "interested", next_state=STATE_AWAITING_CALLBACK_TIME)
    if scenario == "objection":
        # No approved answer to the objection (e.g. no discount policy on
        # file): don't invent one and don't escalate; offer a call with the team.
        return Decision("none", {}, "objection_followup", next_state=STATE_AWAITING_CALLBACK_TIME)
    if scenario == "confused":
        return Decision("none", {}, "confused", next_state=STATE_ENGAGED)
    # No approved answer: never guess — hand the question to a human follow-up.
    return Decision(
        "transfer_to_human",
        {"reason": f"Unanswered customer question: {reason}", "urgency": "low"},
        "unknown_followup",
        next_state=STATE_HANDED_OFF,
    )
