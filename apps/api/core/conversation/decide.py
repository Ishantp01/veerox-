"""Process 4.0 — decide the next action under business policy.

Pure policy: no I/O, no LLM. Given what the customer meant, what we
remember and what approved knowledge matched, pick exactly one approved
action id plus the reply kind. The model never gets to run an action — it
may only *propose* one, and ``validate_action`` rejects anything off the
allow-list or with missing required arguments.
"""

from __future__ import annotations

from typing import Any

from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    STATE_AWAITING_CALLBACK_TIME,
    STATE_CALLBACK_SET,
    STATE_CLOSED,
    STATE_ENGAGED,
    STATE_HANDED_OFF,
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


def decide(
    understanding: Understanding,
    *,
    text: str,
    memory: dict[str, str],
    knowledge: list[Any],
) -> Decision:
    scenario = understanding.scenario
    reason = text.strip()[:_MAX_REASON_LEN]

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
    if scenario == "busy_callback":
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
