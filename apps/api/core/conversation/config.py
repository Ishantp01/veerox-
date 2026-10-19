"""Scenario configuration for the Conversation Intelligence Engine.

A *scenario* is a reusable definition of a situation (quotation 2.3): when it
applies, what the business wants, what the AI may do and must avoid, which
approved actions it may use and where the conversation usually goes next.

The engine ships a built-in library (``DEFAULT_SCENARIOS``). An org can
override any of it, switch scenarios off or add its own through the Scenario
Builder; those edits live in immutable, versioned rows
(``ConversationConfigVersion``). A conversation keeps the version it started
with, so publishing never changes a call that is already running.

Nothing here executes anything: a scenario only *lists* the approved action
ids it allows; ``decide.py`` / ``validate_action`` still gate every action.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.types import ALLOWED_ACTIONS

SCENARIO_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
# What to do when the AI is not sure what the customer meant (blueprint section 8):
# "script" = hand the turn to the org's script (default, safest), "clarify" = ask the
# customer one short clarifying question, "handoff" = give the question to a person.
LOW_CONFIDENCE_ACTIONS = ("script", "clarify", "handoff")
MAX_SCENARIOS = 60
MAX_EXAMPLES = 40

# Scenarios the engine decides on its own even in Smart Script mode: they end
# in a backend action or protect the customer. Everything else is left to the
# org's script, with the scenario's guidance added to the prompt.
SMART_SCRIPT_ENGINE_SCENARIOS = frozenset(
    {
        "human_handoff",
        "angry",
        "not_interested",
        "busy_callback",
        "callback_request",
        "appointment_request",
        "whatsapp_info_request",
        "closing",
    }
)


@dataclass(frozen=True)
class ScenarioDef:
    id: str
    name: str
    situation: str = ""
    examples: tuple[str, ...] = ()
    goal: str = ""
    should_do: str = ""
    must_avoid: str = ""
    actions: tuple[str, ...] = ("none",)
    next_scenarios: tuple[str, ...] = ()
    enabled: bool = True
    # Example of what the AI might say; used as the fallback wording for a
    # custom scenario and shown to the model as a style sample.
    reply_example: str = ""
    builtin: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "situation": self.situation,
            "examples": list(self.examples),
            "goal": self.goal,
            "should_do": self.should_do,
            "must_avoid": self.must_avoid,
            "actions": list(self.actions),
            "next_scenarios": list(self.next_scenarios),
            "enabled": self.enabled,
            "reply_example": self.reply_example,
            "builtin": self.builtin,
        }


def _s(
    id: str,
    name: str,
    situation: str,
    goal: str,
    should_do: str,
    must_avoid: str,
    actions: tuple[str, ...],
    next_scenarios: tuple[str, ...],
    examples: tuple[str, ...] = (),
) -> ScenarioDef:
    return ScenarioDef(
        id=id,
        name=name,
        situation=situation,
        examples=examples,
        goal=goal,
        should_do=should_do,
        must_avoid=must_avoid,
        actions=actions,
        next_scenarios=next_scenarios,
        builtin=True,
    )


# The initial scenario library (quotation 2.3). Rule patterns for the clear
# cases live in understand.py; ``examples`` here are extra, admin-editable
# phrases matched in addition to them and shown to the classifier.
DEFAULT_SCENARIOS: tuple[ScenarioDef, ...] = (
    _s(
        "greeting", "Greeting", "The customer says hello or opens the conversation.",
        "Welcome the customer and invite them to say what they need.",
        "Greet warmly in the customer's language and ask how you can help.",
        "Starting the pitch before the customer has said anything.",
        ("none",), ("identification", "discovery"),
    ),
    _s(
        "identification", "Customer identification",
        "The customer confirms who they are or that they are the right person.",
        "Confirm we are speaking to the right person and note their name.",
        "Thank them, use their name and move to a light discovery question.",
        "Asking for the same details twice; reading a long introduction.",
        ("none",), ("discovery", "busy_callback"),
        ("yes speaking", "haan bol raha hoon", "this is me"),
    ),
    _s(
        "discovery", "Requirement discovery",
        "The customer describes their business, team or what they are struggling with.",
        "Understand the business type and the main pain point before offering anything.",
        "Acknowledge what they said, record business type and pain point, ask ONE follow-up question.",
        "Pitching features before the problem is clear; asking several questions at once.",
        ("none",), ("interested", "price_question", "appointment_request"),
        ("we run a travel agency", "mera clothing business hai", "our problem is follow-up"),
    ),
    _s(
        "interested", "Interested customer", "The customer shows interest or asks for more.",
        "Move the interested customer to a concrete next step such as a call or demo.",
        "Give the approved details and propose a time for a quick call or demo.",
        "Over-promising; pushing when the customer hesitates.",
        ("none", "request_callback", "book_appointment"), ("appointment_request", "price_question"),
    ),
    _s(
        "price_question", "Price question", "The customer asks what it costs.",
        "Give the approved price information clearly.",
        "Answer only from approved pricing knowledge, then ask what suits them.",
        "Quoting a price that is not in approved knowledge.",
        ("none", "send_whatsapp_message", "transfer_to_human"), ("objection", "discount_request", "interested"),
    ),
    _s(
        "objection", "Price objection", "The customer says it is too expensive or hesitates.",
        "Understand whether the concern is budget, value or comparison, without arguing.",
        "Acknowledge, use approved facts and offer a call to go through options.",
        "Arguing; inventing discounts or comparisons.",
        ("none", "request_callback", "transfer_to_human"), ("discount_request", "interested", "not_interested"),
    ),
    _s(
        "discount_request", "Discount request", "The customer asks for a discount or a better price.",
        "Handle the request honestly without promising anything that is not approved.",
        "Share only approved offers; otherwise say the team will confirm the best price and offer a call.",
        "Promising a discount; inventing offers.",
        ("none", "request_callback", "transfer_to_human"), ("interested", "appointment_request", "not_interested"),
        ("any offer", "koi offer hai", "can you reduce the price"),
    ),
    _s(
        "busy_callback", "Customer busy", "The customer cannot talk now.",
        "Respect their time and get a useful follow-up time.",
        "Acknowledge, ask a suitable callback time, schedule the callback, end politely.",
        "Continuing the pitch; repeating features; pressuring the customer.",
        ("none", "request_callback"), ("callback_request", "closing"),
        ("I am in a meeting", "I am busy", "abhi busy hoon"),
    ),
    _s(
        "callback_request", "Callback request", "The customer asks to be called at a specific time.",
        "Schedule the callback for the time they gave.",
        "Confirm the time back to them and schedule the callback.",
        "Asking again for a time they already gave.",
        ("none", "request_callback"), ("closing",),
        ("please call me at 6", "shaam ko call karna", "call me tomorrow morning"),
    ),
    _s(
        "whatsapp_info_request", "WhatsApp information request",
        "The customer asks for details to be sent on WhatsApp.",
        "Send the approved details on WhatsApp.",
        "Send only approved knowledge text and say it has been sent.",
        "Sending text that is not approved knowledge.",
        ("none", "send_whatsapp_message", "transfer_to_human"), ("interested", "closing"),
    ),
    _s(
        "not_interested", "Not interested", "The customer declines or asks not to be contacted.",
        "Accept the decision politely and stop.",
        "Thank them, mark them not interested and close.",
        "Trying to change their mind; calling or messaging again.",
        ("none", "mark_not_interested"), ("closing",),
    ),
    _s(
        "angry", "Angry customer", "The customer is upset or complaining.",
        "De-escalate and get a person to help.",
        "Apologise sincerely, do not argue and hand over to a team member.",
        "Pitching; defending the company; arguing.",
        ("none", "transfer_to_human"), ("human_handoff", "closing"),
    ),
    _s(
        "confused", "Confused customer", "The customer does not understand.",
        "Clear up the confusion simply.",
        "Apologise briefly, explain in one simple sentence and ask what they want to know.",
        "Repeating the same explanation; jargon.",
        ("none",), ("discovery", "interested"),
    ),
    _s(
        "unknown_question", "Unknown question", "The customer asks something not covered by approved knowledge.",
        "Never guess; get the right answer to the customer.",
        "Say honestly that the team will confirm and arrange a follow-up.",
        "Making up facts, prices or policies.",
        ("none", "transfer_to_human"), ("closing",),
    ),
    _s(
        "appointment_request", "Appointment request", "The customer wants to book a demo or meeting.",
        "Book the appointment for the day and time they choose.",
        "Ask for a day and time if missing, then confirm the booking.",
        "Booking without a stated date and time.",
        ("none", "book_appointment"), ("closing",),
    ),
    _s(
        "human_handoff", "Human handoff", "The customer asks to talk to a person.",
        "Hand the customer to a team member quickly.",
        "Confirm that a teammate will contact them and pass on the reason.",
        "Resisting or making them repeat the request.",
        ("none", "transfer_to_human"), ("closing",),
    ),
    _s(
        "closing", "Closing", "The customer is wrapping up the conversation.",
        "End the conversation warmly.",
        "Thank them and say goodbye briefly.",
        "Starting a new pitch or asking more questions.",
        ("none",), (),
        ("thank you, that is all", "ok bye", "bas itna hi"),
    ),
)

_DEFAULT_BY_ID = {s.id: s for s in DEFAULT_SCENARIOS}


@dataclass
class EngineConfig:
    """The scenario set in force for one conversation."""

    scenarios: dict[str, ScenarioDef]
    version_id: UUID | None = None
    version: int = 0  # 0 = the built-in defaults
    custom_ids: frozenset[str] = field(default_factory=frozenset)
    low_confidence_action: str = "script"
    # Opt-in per org (Scenario Builder setting): the context-aware dialogue policy in
    # dialogue.py. False = the engine behaves exactly as it always has.
    enhanced_dialogue: bool = False

    def get(self, scenario_id: str) -> ScenarioDef | None:
        return self.scenarios.get(scenario_id)

    def is_enabled(self, scenario_id: str) -> bool:
        scenario = self.scenarios.get(scenario_id)
        return scenario is not None and scenario.enabled

    def ids(self) -> list[str]:
        return list(self.scenarios)

    def enabled_ids(self) -> list[str]:
        return [s.id for s in self.scenarios.values() if s.enabled]


def default_config() -> EngineConfig:
    return EngineConfig(scenarios={s.id: s for s in DEFAULT_SCENARIOS})


def _clean_list(value: Any, limit: int, item_len: int = 200) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value[:limit]:
        text = str(item).strip()[:item_len]
        if text and text not in out:
            out.append(text)
    return tuple(out)


def validate_config_payload(data: Any) -> list[str]:
    """Human-readable problems with a submitted config; empty when valid."""
    problems: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        return ["config must be an object with a 'scenarios' list"]
    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        return ["settings must be an object"]
    if settings.get("low_confidence_action", "script") not in LOW_CONFIDENCE_ACTIONS:
        problems.append(
            "settings.low_confidence_action must be one of: " + ", ".join(LOW_CONFIDENCE_ACTIONS)
        )
    if "enhanced_dialogue" in settings and not isinstance(settings["enhanced_dialogue"], bool):
        problems.append("settings.enhanced_dialogue must be true or false")
    items = data["scenarios"]
    if len(items) > MAX_SCENARIOS:
        problems.append(f"at most {MAX_SCENARIOS} scenarios are allowed")
    seen: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            problems.append(f"scenario #{index + 1} must be an object")
            continue
        sid = str(raw.get("id") or "")
        if not SCENARIO_ID_RE.match(sid):
            problems.append(
                f"scenario #{index + 1}: id must be lowercase letters, digits or _ (2-31 chars, start with a letter)"
            )
            continue
        if sid in seen:
            problems.append(f"duplicate scenario id '{sid}'")
        seen.add(sid)
        if not str(raw.get("name") or "").strip():
            problems.append(f"scenario '{sid}' needs a name")
        for action in raw.get("actions") or ["none"]:
            if action not in ALLOWED_ACTIONS:
                problems.append(f"scenario '{sid}': '{action}' is not an approved action")
        if len(raw.get("examples") or []) > MAX_EXAMPLES:
            problems.append(f"scenario '{sid}': at most {MAX_EXAMPLES} examples")
        is_builtin = sid in _DEFAULT_BY_ID
        if not is_builtin and raw.get("enabled", True):
            if not str(raw.get("goal") or "").strip():
                problems.append(f"custom scenario '{sid}' needs a goal")
            if not (raw.get("examples") or []):
                problems.append(f"custom scenario '{sid}' needs at least one example phrase")
    for raw in items:
        if isinstance(raw, dict):
            for nxt in raw.get("next_scenarios") or []:
                if nxt not in seen and nxt not in _DEFAULT_BY_ID:
                    problems.append(f"scenario '{raw.get('id')}': next scenario '{nxt}' does not exist")
    return problems


def config_from_json(
    data: dict[str, Any] | None, *, version_id: UUID | None = None, version: int = 0
) -> EngineConfig:
    """Built-in library overlaid with the org's stored scenarios. Built-ins the
    org never touched keep their defaults; unknown/invalid entries are skipped
    (a stored config was validated when it was saved)."""
    scenarios: dict[str, ScenarioDef] = {s.id: s for s in DEFAULT_SCENARIOS}
    custom: set[str] = set()
    for raw in (data or {}).get("scenarios", []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "")
        if not SCENARIO_ID_RE.match(sid):
            continue
        base = _DEFAULT_BY_ID.get(sid)
        actions = tuple(a for a in _clean_list(raw.get("actions"), 8, 40) if a in ALLOWED_ACTIONS) or (
            base.actions if base else ("none",)
        )
        scenarios[sid] = ScenarioDef(
            id=sid,
            name=str(raw.get("name") or (base.name if base else sid))[:80],
            situation=str(raw.get("situation") or "")[:500],
            examples=_clean_list(raw.get("examples"), MAX_EXAMPLES),
            goal=str(raw.get("goal") or "")[:500],
            should_do=str(raw.get("should_do") or "")[:800],
            must_avoid=str(raw.get("must_avoid") or "")[:800],
            actions=actions,
            next_scenarios=_clean_list(raw.get("next_scenarios"), 10, 40),
            enabled=bool(raw.get("enabled", True)),
            reply_example=str(raw.get("reply_example") or "")[:500],
            builtin=base is not None,
        )
        if base is None:
            custom.add(sid)
    raw_settings = data.get("settings") if isinstance(data, dict) else None
    low_action = (raw_settings or {}).get("low_confidence_action", "script") if isinstance(raw_settings, dict) else "script"
    if low_action not in LOW_CONFIDENCE_ACTIONS:
        low_action = "script"
    enhanced = isinstance(raw_settings, dict) and raw_settings.get("enhanced_dialogue") is True
    return EngineConfig(
        scenarios, version_id, version, frozenset(custom), low_action, enhanced_dialogue=enhanced
    )


def scenario_guidance(scenario: ScenarioDef | None) -> str:
    """The scenario's goal / do / avoid as one prompt block."""
    if scenario is None:
        return ""
    parts = [f"Situation: {scenario.name}."]
    if scenario.goal:
        parts.append(f"Goal: {scenario.goal}")
    if scenario.should_do:
        parts.append(f"Do: {scenario.should_do}")
    if scenario.must_avoid:
        parts.append(f"Avoid: {scenario.must_avoid}")
    if scenario.next_scenarios:
        parts.append("Afterwards the conversation usually moves to: " + ", ".join(scenario.next_scenarios) + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Loading: active version, and the per-conversation pin.
# ---------------------------------------------------------------------------

_ACTIVE_TTL_SECONDS = 20.0
_active_cache: dict[UUID, tuple[float, EngineConfig]] = {}
_version_cache: OrderedDict[UUID, EngineConfig] = OrderedDict()
_pin_cache: OrderedDict[UUID, UUID | None] = OrderedDict()
_CACHE_MAX = 500


def reset_config_cache() -> None:
    _active_cache.clear()
    _version_cache.clear()
    _pin_cache.clear()


def invalidate_active(org_id: UUID) -> None:
    _active_cache.pop(org_id, None)


def _remember(cache: OrderedDict[Any, Any], key: Any, value: Any) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_MAX:
        cache.popitem(last=False)


async def load_version_config(db: AsyncSession, org_id: UUID, version_id: UUID) -> EngineConfig | None:
    from apps.api.db.models.conversation_engine import ConversationConfigVersion

    cached = _version_cache.get(version_id)
    if cached is not None:
        return cached
    row = await db.get(ConversationConfigVersion, version_id)
    if row is None or row.org_id != org_id:
        return None
    config = config_from_json(row.config, version_id=row.id, version=row.version or 0)
    if row.status == "published":  # drafts are mutable, never cached
        _remember(_version_cache, version_id, config)
    return config


async def load_active_config(db: AsyncSession, org_id: UUID) -> EngineConfig:
    """Latest published version for the org, or the built-in defaults."""
    from apps.api.db.models.conversation_engine import ConversationConfigVersion

    cached = _active_cache.get(org_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _ACTIVE_TTL_SECONDS:
        return cached[1]
    row = (
        await db.execute(
            select(ConversationConfigVersion)
            .where(
                ConversationConfigVersion.org_id == org_id,
                ConversationConfigVersion.status == "published",
            )
            .order_by(ConversationConfigVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    config = (
        config_from_json(row.config, version_id=row.id, version=row.version or 0)
        if row is not None
        else default_config()
    )
    _active_cache[org_id] = (now, config)
    return config


async def resolve_config(db: AsyncSession, org_id: UUID, conversation_id: UUID | None) -> EngineConfig:
    """The configuration for this turn.

    A conversation keeps the version its first logged turn used; only a
    conversation with no turns yet takes the currently published version. So
    publishing or rolling back never changes a call that is under way.
    """
    from apps.api.db.models.conversation_engine import ConversationTurn

    if conversation_id is None:
        return await load_active_config(db, org_id)
    if conversation_id in _pin_cache:
        pinned = _pin_cache[conversation_id]
        if pinned is None:
            return default_config()
        loaded = await load_version_config(db, org_id, pinned)
        if loaded is not None:
            return loaded
    first = (
        await db.execute(
            select(ConversationTurn.config_version_id)
            .where(
                ConversationTurn.org_id == org_id,
                ConversationTurn.conversation_id == conversation_id,
            )
            .order_by(ConversationTurn.created_at.asc())
            .limit(1)
        )
    ).first()
    if first is not None:
        version_id = first[0]
        _remember(_pin_cache, conversation_id, version_id)
        if version_id is None:
            return default_config()
        loaded = await load_version_config(db, org_id, version_id)
        return loaded if loaded is not None else default_config()
    return await load_active_config(db, org_id)
