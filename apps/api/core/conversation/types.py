"""Shared types and constants for the Conversation Intelligence Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Scenario ids the engine understands (quotation §2.2 priority scenarios).
SCENARIOS = (
    "busy_callback",
    "price_question",
    "objection",
    "interested",
    "not_interested",
    "confused",
    "angry",
    "unknown_question",
    "human_handoff",
    "greeting",
    "appointment_request",
    "whatsapp_info_request",
    # Added with the Scenario Builder (quotation 2.3 initial library).
    "identification",
    "discovery",
    "discount_request",
    "callback_request",
    "closing",
)

EMOTIONS = ("neutral", "positive", "negative", "angry", "confused")

LANGUAGES = ("hi", "en", "hinglish")

# Action ids the engine may request. "none" = reply only. Everything else is
# the name of an existing tool in core/tools.py::DISPATCH_TABLE — the backend
# runs it, the model never does.
ALLOWED_ACTIONS = frozenset(
    {
        "none",
        "request_callback",
        "transfer_to_human",
        "mark_not_interested",
        "book_appointment",
        "send_whatsapp_message",
    }
)

# Structured memory keys the engine may store (quotation §2.1).
MEMORY_KEYS = (
    "name",
    "business_type",
    "pain_point",
    "team_size",
    "interest_level",
    "preferred_channel",
    "callback_time",
)

# Conversation states, stored on each ConversationTurn.
STATE_NEW = "new"
STATE_ENGAGED = "engaged"
STATE_AWAITING_CALLBACK_TIME = "awaiting_callback_time"
STATE_CALLBACK_SET = "callback_set"
STATE_AWAITING_APPOINTMENT_TIME = "awaiting_appointment_time"
STATE_APPOINTMENT_SET = "appointment_set"
STATE_INFO_SENT = "info_sent"
STATE_HANDED_OFF = "handed_off"
STATE_CLOSED = "closed"
STATE_DISCOVERY = "discovery"
STATE_QUALIFICATION = "qualification"
STATE_OBJECTION = "objection"
# Repeated silence on a call: the caller is probably not hearing us.
STATE_CONNECTION_ISSUE = "connection_issue"


@dataclass
class Understanding:
    """Output of process 2.0 — what the customer meant."""

    scenario: str
    intent: str
    emotion: str
    language: str
    confidence: float
    source: str  # "rules" | "llm"
    # Facts pulled out of the text: any of MEMORY_KEYS plus callback_date /
    # callback_time_hhmm (ISO date + 24h time) when they were stated.
    facts: dict[str, str] = field(default_factory=dict)
    # Optional action id proposed by the LLM; validated by the engine.
    proposed_action: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    # Enhanced dialogue (core/conversation/dialogue.py); all empty unless the org opted in.
    signals: frozenset[str] = frozenset()
    corrections: dict[str, str] = field(default_factory=dict)  # memory key -> earlier value
    conflicts: dict[str, str] = field(default_factory=dict)


@dataclass
class Decision:
    """Output of process 4.0 — what to do about it."""

    action_id: str
    args: dict[str, Any]
    # Reply kind consumed by reply.py: "answer", "ask_callback_time",
    # "callback_confirmed", "handoff", "angry_handoff", "not_interested",
    # "unknown_followup", "interested", "confused", "greeting".
    reply_kind: str
    knowledge: list[Any] = field(default_factory=list)
    next_state: str = STATE_ENGAGED
    # Enhanced dialogue only: extra per-turn instructions for the speaking model, and whether
    # the reply may nudge toward a call. Defaults leave every base reply unchanged.
    directives: tuple[str, ...] = ()
    push: bool = True


@dataclass
class EngineResult:
    """What the engine hands back to AgentCore."""

    reply: str | None  # None => caller must use the legacy script path
    fallback_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    # Smart Script mode: scenario guidance to add to the script prompt when the
    # engine hands the turn to the script path.
    guidance: str | None = None
