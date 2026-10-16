"""Live voice decisions (the Decision Layer for phone calls).

The voice agent speaks through the realtime model, so the engine does not
write the reply. It decides, then hands the speaking model a short
instruction for just that turn ("the callback is booked for 5 pm, confirm it
briefly"). Everything here is built so that it can never hurt a call:

* Nothing happens unless the engine is allowed globally
  (``CONVERSATION_VOICE_LIVE``) and the org is on ``human_conversation`` with
  ``conversation_voice_mode = "live"``.
* ``compute_voice_plan`` only *decides*. It runs in the background while the
  existing answer gap elapses, and returns ``None`` on any doubt (timeout,
  invalid output, low confidence, error): the call then carries on exactly as
  it does today.
* ``apply_voice_plan`` runs the approved action, and is only called once the
  adapter is actually about to use the decision, so an action can never run
  for a decision that was dropped for being late.
* The engine executes the action and tells the model the result, and the
  adapter disables tool use for that response, so a callback is never booked
  twice (once by the engine, once by the model).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from apps.api.config import settings
from apps.api.core.conversation.config import (
    SMART_SCRIPT_ENGINE_SCENARIOS,
    EngineConfig,
    resolve_config,
    scenario_guidance,
)
from apps.api.core.conversation.decide import decide, validate_action
from apps.api.core.conversation.engine import (
    _default_llm,
    _last_state,
    _log_turn,
    wants_knowledge,
)
from apps.api.core.conversation.knowledge import find_knowledge
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    MEMORY_KEYS,
    STATE_CONNECTION_ISSUE,
    Decision,
    Understanding,
)
from apps.api.core.conversation.understand import UnderstandError, understand
from apps.api.core.memory import load_last_n
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.db.models.conversation_engine import ConversationTurn
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# (action_id, args) -> tool result. Supplied by the voice adapter.
Dispatch = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

_FLAG_TTL_SECONDS = 30.0
# org id -> (checked at, mode) where mode is the org's voice mode while it is on
# human_conversation ("off" / "shadow" / "live") and "none" otherwise.
_mode_cache: dict[UUID, tuple[float, str]] = {}
# org id -> True while the org is on Smart Script (refreshed with _mode_cache).
_smart_cache: dict[UUID, bool] = {}

_LANGUAGE_NAME = {
    "en": "English",
    "hi": "Hindi",
    "hinglish": "Hinglish (Hindi written in Latin letters)",
}


@dataclass
class VoicePlan:
    """A decision that has been made but not yet applied."""

    understanding: Understanding
    decision: Decision
    memory: dict[str, str]
    state_before: str | None
    started: float
    # The scenario's goal / do / avoid, for guided replies and Smart Script.
    guidance: str = ""
    config: EngineConfig | None = None


@dataclass
class VoiceReply:
    """What the adapter sends to the speaking model."""

    instruction: str
    # True when the engine already executed an action this turn, so the model
    # must not call a tool itself.
    tool_choice_none: bool


async def org_voice_mode(org_id: UUID) -> str:
    """What the engine does on this org's calls: "live", "shadow", "off", or
    "none" (the org isn't on Human Conversation). One DB read per org every
    few seconds, so ordinary calls pay nothing for the check."""
    cached = _mode_cache.get(org_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _FLAG_TTL_SECONDS:
        return cached[1]
    async with AsyncSessionLocal() as db:
        org = await db.get(Org, org_id)
    mode = "none"
    if org is not None and org.conversation_mode in ("human_conversation", "smart_script"):
        mode = org.conversation_voice_mode
    _mode_cache[org_id] = (now, mode)
    _smart_cache[org_id] = org is not None and org.conversation_mode == "smart_script"
    return mode


def cached_voice_mode(org_id: UUID) -> tuple[str | None, bool]:
    """(last known mode, still fresh?) without touching the database or
    waiting. ``(None, False)`` for an org not seen yet. The call path uses this
    so that an org that is not on live voice can never be slowed down."""
    cached = _mode_cache.get(org_id)
    if cached is None:
        return None, False
    return cached[1], time.monotonic() - cached[0] < _FLAG_TTL_SECONDS


def set_cached_voice_mode(org_id: UUID, mode: str) -> None:
    """Seed the cache (used by tests and by anything that just changed the setting)."""
    _mode_cache[org_id] = (time.monotonic(), mode)


async def voice_live_enabled(org_id: UUID) -> bool:
    """Global ceiling AND this org opted in. A switch change takes effect
    within a few seconds."""
    if not settings.conversation_voice_live:
        return False
    return await org_voice_mode(org_id) == "live"


def forget_org(org_id: UUID) -> None:
    """Drop the cached mode for one org, so a mode change takes effect at once."""
    _mode_cache.pop(org_id, None)
    _smart_cache.pop(org_id, None)


def reset_flag_cache() -> None:
    _mode_cache.clear()
    _smart_cache.clear()


async def compute_voice_plan(
    org_id: UUID, user_id: UUID, conversation_id: UUID, text: str
) -> VoicePlan | None:
    """Decide what to do about the caller's latest turn, or ``None`` to leave
    the call exactly as it is. Never raises."""
    started = time.monotonic()
    try:
        if not text or not await voice_live_enabled(org_id):
            return None
        async with AsyncSessionLocal() as db:
            org = await db.get(Org, org_id)
            api_key = resolve_openai_api_key(org)
            state = await _last_state(db, org_id, conversation_id)
            memory = await load_memory(db, org_id, user_id)
            history = await load_last_n(db, user_id, n=4)
            config = await resolve_config(db, org_id, conversation_id)
            understanding: Understanding | None = None
            fallback: str | None = None
            try:
                understanding = await understand(
                    text,
                    state=state,
                    history=history,
                    memory=memory,
                    llm=_default_llm(api_key),
                    timeout=settings.conversation_voice_llm_timeout_seconds,
                    config=config,
                )
            except UnderstandError as exc:
                fallback = exc.reason
            if understanding is not None:
                if (
                    understanding.proposed_action is not None
                    and understanding.proposed_action not in ALLOWED_ACTIONS
                ):
                    fallback = "invalid_action"
                elif understanding.confidence < settings.conversation_min_confidence:
                    fallback = "low_confidence"
            if fallback is not None:
                await _log_turn(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    channel="voice",
                    mode="live",
                    text=text,
                    started=started,
                    understanding=understanding,
                    state=state,
                    fallback_reason=fallback,
                    config=config,
                )
                return None
            assert understanding is not None
            if _smart_cache.get(org_id) and (
                understanding.scenario not in SMART_SCRIPT_ENGINE_SCENARIOS
            ):
                # Smart Script: keep the org's script, add this scenario's guidance.
                guidance = scenario_guidance(config.get(understanding.scenario))
                if not guidance:
                    return None
                return VoicePlan(
                    understanding,
                    Decision("none", {}, "script_guidance", next_state=state or "engaged"),
                    memory,
                    state,
                    started,
                    guidance,
                    config,
                )
            knowledge: list[Any] = []
            if wants_knowledge(understanding.scenario, config):
                knowledge = await find_knowledge(db, org_id, text, understanding.language)
            decision = decide(
                understanding,
                text=text,
                memory=memory,
                knowledge=knowledge,
                channel="voice",
                config=config,
            )
            if not validate_action(decision.action_id, decision.args):
                await _log_turn(
                    db,
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    channel="voice",
                    mode="live",
                    text=text,
                    started=started,
                    understanding=understanding,
                    decision=decision,
                    state=state,
                    fallback_reason="invalid_action",
                    config=config,
                )
                return None
            defined = config.get(understanding.scenario)
            return VoicePlan(
                understanding,
                decision,
                memory,
                state,
                started,
                scenario_guidance(defined) if decision.reply_kind == "guided" else "",
                config,
            )
    except Exception:  # noqa: BLE001 — a decision problem must never reach the caller
        logger.warning("voice_decision_failed", org_id=str(org_id), exc_info=True)
        return None


def build_instruction(plan: VoicePlan, *, action_result: dict[str, Any] | None) -> str:
    """The short per-turn instruction for the speaking model."""
    decision = plan.decision
    language = _LANGUAGE_NAME.get(plan.understanding.language, "English")
    lead = (
        f"Reply in {language}, the language the caller just used, in one or two short "
        "spoken sentences. "
    )
    kind = decision.reply_kind
    when = ""
    if kind in ("callback_confirmed", "appointment_confirmed"):
        when = f"{decision.args.get('date')} at {decision.args.get('time')}"

    if kind == "ask_callback_time":
        body = (
            "The caller can't talk now. Do not continue the pitch. Acknowledge politely and "
            "ask what time we should call back."
        )
    elif kind == "callback_confirmed":
        body = f"A callback is now scheduled for {when}. Confirm it briefly and thank them."
    elif kind == "ask_appointment_time":
        body = "The caller wants an appointment. Ask which day and time suit them."
    elif kind == "appointment_confirmed":
        body = f"The appointment is now booked for {when}. Confirm it briefly and thank them."
    elif kind in ("handoff", "unknown_followup"):
        body = (
            "A teammate has been asked to follow up with the caller. Say so briefly. "
            "Do not guess any answer."
            if kind == "handoff"
            else "You do not have an approved answer to this question. Say honestly that the "
            "team will confirm and get back to them. Do not guess."
        )
    elif kind == "angry_handoff":
        body = (
            "The caller is upset. Apologise sincerely, then say a teammate will contact them "
            "right away. Do not argue or pitch."
        )
    elif kind == "not_interested":
        body = (
            "The caller declined and has been marked not interested. Thank them politely "
            "and close the call. Do not pitch again."
        )
    elif kind == "whatsapp_sent":
        body = "The details were just sent to the caller's WhatsApp. Tell them to have a look."
    elif kind == "answer":
        facts = "\n".join(f"- {item.title}: {item.body}" for item in decision.knowledge)
        body = (
            "Answer using ONLY these approved facts. Add no prices, dates or promises that "
            "are not in them. If they do not fully answer the question, say the team will "
            f"confirm the rest.\nApproved facts:\n{facts}"
        )
    elif kind == "identification":
        body = "Thank the caller, use their name if known, and ask what kind of business they run."
    elif kind == "discovery_business":
        body = "Acknowledge what they said and ask what kind of business they run."
    elif kind == "discovery_pain":
        body = "Thank them for sharing and ask ONE question: the biggest challenge they face right now."
    elif kind == "discovery_offer":
        body = "Acknowledge their challenge, say we can help and offer a quick call or demo."
    elif kind == "discount_followup":
        body = (
            "Do not promise any discount or offer. Say the team can share the best offer and ask "
            "when a quick call would suit them."
        )
    elif kind == "closing":
        body = "The caller is wrapping up. Thank them warmly and say goodbye. Do not pitch again."
    elif kind == "guided":
        body = (
            f"Follow this situation guidance: {plan.guidance or 'help the caller politely'} "
            "Never invent prices, dates or promises."
        )
    elif kind == "script_guidance":
        body = (
            "Carry on with your script, but apply this guidance for this turn: "
            f"{plan.guidance} Do not mention that you were given guidance."
        )
    elif kind == "objection_followup":
        body = (
            "The caller raised an objection. Acknowledge it without arguing, and offer a "
            "call with the team to go through the options."
        )
    elif kind == "interested":
        body = "The caller sounds interested. Offer a quick call or demo and ask when suits them."
    elif kind == "confused":
        body = "The caller is confused. Apologise briefly and ask what they would like to know."
    else:  # greeting and anything else
        body = "Greet the caller warmly and ask how you can help."

    if action_result is not None and action_result.get("status") == "error":
        body += " The action did not complete; do not claim it did."
    # Continuity: what we already know about the caller, so the agent neither
    # asks for it again nor repeats itself.
    known = {**plan.memory, **{k: v for k, v in plan.understanding.facts.items() if k in MEMORY_KEYS}}
    known = {k: v for k, v in known.items() if v}
    if known:
        body += " Already known about the caller (do not ask for it again): " + "; ".join(
            f"{k}: {v}" for k, v in known.items()
        ) + "."
    body += " Do not repeat information you have already given in this call."
    return f"DECISION FOR THIS TURN: {lead}{body}"


async def apply_voice_plan(
    plan: VoicePlan,
    *,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    text: str,
    dispatch: Dispatch,
    done_actions: set[str],
) -> VoiceReply | None:
    """Run the approved action (once) and produce the instruction. Returns
    ``None`` when the action failed, so the caller falls back to today's
    behaviour. Never raises."""
    decision = plan.decision
    action_result: dict[str, Any] | None = None
    executed = False
    try:
        if decision.action_id != "none":
            key = f"{conversation_id}:{decision.action_id}:{sorted(decision.args.items())}"
            if key in done_actions:
                # Already ran for this call (e.g. the caller repeated themselves):
                # don't run it again, just let the model confirm.
                action_result = {"status": "ok", "duplicate": True}
            else:
                action_result = await dispatch(decision.action_id, decision.args)
                if action_result.get("status") == "error":
                    async with AsyncSessionLocal() as db:
                        await _log_turn(
                            db,
                            org_id=org_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            channel="voice",
                            mode="live",
                            text=text,
                            started=plan.started,
                            understanding=plan.understanding,
                            decision=decision,
                            state=plan.state_before,
                            action_result=action_result,
                            fallback_reason="action_failed",
                            config=plan.config,
                        )
                    return None
                done_actions.add(key)
                executed = True
        instruction = build_instruction(plan, action_result=action_result)
        async with AsyncSessionLocal() as db:
            facts = {k: v for k, v in plan.understanding.facts.items() if k in MEMORY_KEYS}
            try:
                await save_facts(db, org_id, user_id, facts)
                await db.commit()
            except Exception:  # noqa: BLE001 — memory is best effort
                logger.warning("voice_memory_save_failed", exc_info=True)
                await db.rollback()
            await _log_turn(
                db,
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                channel="voice",
                mode="live",
                text=text,
                started=plan.started,
                understanding=plan.understanding,
                decision=decision,
                state=decision.next_state,
                knowledge_ids=[str(item.id) for item in decision.knowledge],
                action_result=action_result,
                reply=f"[voice instruction] {instruction}",
                config=plan.config,
            )
        return VoiceReply(instruction=instruction, tool_choice_none=executed)
    except Exception:  # noqa: BLE001
        logger.warning("voice_decision_apply_failed", exc_info=True)
        return None


async def log_silence_event(
    org_id: UUID, user_id: UUID, conversation_id: UUID, *, kind: str
) -> None:
    """Turn-log row for a silence prompt ("check") or the call entering the
    connection-issue state. Best effort: never raises."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                ConversationTurn(
                    org_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    channel="voice",
                    mode="live",
                    user_text="(silence)",
                    state=STATE_CONNECTION_ISSUE if kind == "connection_issue" else None,
                    intent=kind,
                    scenario="silence",
                    reply_text=(
                        "[silence] caller unreachable, ending politely"
                        if kind == "connection_issue"
                        else "[silence] gentle check"
                    ),
                    latency_ms=0,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("voice_silence_log_failed", exc_info=True)
