"""The Conversation Intelligence Engine — orchestrates processes 1.0–6.0.

``run_engine`` never raises: any problem becomes an ``EngineResult`` with
``reply=None`` and a ``fallback_reason``, which tells AgentCore to use the
legacy script/tool-calling path instead. Every run (answered or fallen back,
live or shadow) writes one ``ConversationTurn`` row.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation import dialogue, runtime_stats
from apps.api.core.conversation.config import (
    SMART_SCRIPT_ENGINE_SCENARIOS,
    EngineConfig,
    resolve_config,
    scenario_guidance,
)
from apps.api.core.conversation.decide import decide, low_confidence_decision, validate_action
from apps.api.core.conversation.knowledge import find_knowledge
from apps.api.core.conversation.memory import forget_facts, load_memory, save_facts
from apps.api.core.conversation.outcome import mark_lead_interested, record_disposition
from apps.api.core.conversation.reply import build_reply
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    MEMORY_KEYS,
    STATE_ENGAGED,
    Decision,
    EngineResult,
    Understanding,
)
from apps.api.core.conversation.understand import (
    _BUSY_CUE_RE,
    LLMCall,
    UnderstandError,
    understand,
)
from apps.api.core.llm import ChatResult, chat_completion
from apps.api.db.models.conversation_engine import ConversationTurn

logger = structlog.get_logger(__name__)

# (action_id, args) -> tool result dict. Supplied by AgentCore so the engine
# runs actions through the existing tools without importing the agent module.
Dispatch = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Scenarios that may be answered from approved knowledge.
_KNOWLEDGE_SCENARIOS = frozenset(
    {
        "price_question",
        "objection",
        "interested",
        "confused",
        "unknown_question",
        "whatsapp_info_request",
        "discount_request",
    }
)


def wants_knowledge(
    scenario: str, config: EngineConfig | None, understanding: Understanding | None = None
) -> bool:
    """Built-in knowledge scenarios, plus every business-defined one (it may
    be answered from approved facts, never from the model's own). With the enhanced
    dialogue mode, any message that asks about a topic (investment, profit, ...) also
    looks for approved facts, whatever scenario it landed in."""
    if scenario in _KNOWLEDGE_SCENARIOS:
        return True
    if (
        config is not None
        and config.enhanced_dialogue
        and understanding is not None
        and any(s.startswith("topic:") for s in understanding.signals)
    ):
        return True
    defined = config.get(scenario) if config is not None else None
    return defined is not None and not defined.builtin


_CALL_NOW_RE = re.compile(
    r"\b(call\s*me|call\s*kar(?:o|do|na|iye|iyega)?|mujhe\s*call|phone\s*kar(?:o|do)?|"
    r"give\s*me\s*a\s*call|ring\s*me)\b",
    re.IGNORECASE,
)
# A call asked for "later"/at some time is a scheduled callback, not a call right now.
_CALL_LATER_RE = re.compile(
    r"\b(later|baad|kal|tomorrow|tonight|evening|morning|afternoon|shaam|subah|dopahar|raat|"
    r"next|mon|tue|wed|thu|fri|sat|sun)\w*\b|\d",
    re.IGNORECASE,
)


def wants_call_now(
    understanding: Understanding, decision: Decision, text: str, channel: str
) -> bool:
    """True when a WhatsApp customer plainly asks to be called right now ("call me",
    "call karo") and the rules found no callback time to schedule instead."""
    return (
        channel == "whatsapp"
        and understanding.scenario in ("callback_request", "busy_callback")
        and decision.reply_kind == "ask_callback_time"
        and _CALL_NOW_RE.search(text) is not None
        and _CALL_LATER_RE.search(text) is None
        and _BUSY_CUE_RE.search(text) is None
    )


def _default_llm(api_key: str | None) -> LLMCall:
    async def call(messages: list[dict[str, Any]]) -> ChatResult:
        return await chat_completion(messages, temperature=0.0, max_tokens=300, api_key=api_key)

    return call


async def _last_state(db: AsyncSession, org_id: UUID, conversation_id: UUID | None) -> str | None:
    if conversation_id is None:
        return None
    stmt = (
        select(ConversationTurn.state)
        .where(
            ConversationTurn.org_id == org_id,
            ConversationTurn.conversation_id == conversation_id,
            ConversationTurn.fallback_reason.is_(None),
        )
        .order_by(ConversationTurn.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _log_turn(
    db: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID | None,
    channel: str,
    mode: str,
    text: str,
    started: float,
    understanding: Understanding | None = None,
    decision: Decision | None = None,
    state: str | None = None,
    knowledge_ids: list[str] | None = None,
    action_result: dict[str, Any] | None = None,
    reply: str | None = None,
    fallback_reason: str | None = None,
    config: EngineConfig | None = None,
) -> None:
    """Best-effort: a logging failure must never break the customer's turn."""
    runtime_stats.record(channel, fallback_reason or "ok", (time.monotonic() - started) * 1000)
    try:
        db.add(
            ConversationTurn(
                org_id=org_id,
                user_id=user_id,
                conversation_id=conversation_id,
                channel=channel,
                mode=mode,
                user_text=text,
                state=state,
                intent=understanding.intent if understanding else None,
                scenario=understanding.scenario if understanding else None,
                emotion=understanding.emotion if understanding else None,
                language=understanding.language if understanding else None,
                confidence=understanding.confidence if understanding else None,
                source=understanding.source if understanding else None,
                knowledge_ids=knowledge_ids or None,
                action_id=decision.action_id if decision else None,
                action_result=action_result,
                reply_text=reply,
                fallback_reason=fallback_reason,
                latency_ms=int((time.monotonic() - started) * 1000),
                config_version_id=config.version_id if config else None,
                config_version=config.version if config else None,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("conversation_turn_log_failed", exc_info=True)
        await db.rollback()


async def _save_facts(
    db: AsyncSession,
    org_id: UUID,
    user_id: UUID,
    understanding: Understanding,
    *,
    extra: dict[str, str] | None = None,
    forget: list[str] | None = None,
    enhanced: bool = False,
) -> None:
    facts = {k: v for k, v in understanding.facts.items() if k in MEMORY_KEYS}
    allowed = MEMORY_KEYS
    if enhanced:
        facts.update(extra or {})
        allowed = (*MEMORY_KEYS, *dialogue.ENHANCED_MEMORY_KEYS)
    try:
        await save_facts(db, org_id, user_id, facts, allowed_keys=allowed)
        if forget:
            await forget_facts(db, org_id, user_id, forget)
        await db.commit()
    except Exception:  # noqa: BLE001 — memory is best effort
        logger.warning("conversation_memory_save_failed", exc_info=True)
        await db.rollback()


async def run_engine(
    db: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    conversation_id: UUID | None,
    channel: str,
    text: str,
    history: list[dict[str, Any]],
    api_key: str | None,
    dispatch: Dispatch | None,
    mode: str = "live",
    llm: LLMCall | None = None,
    config: EngineConfig | None = None,
    smart_script: bool = False,
) -> EngineResult:
    """Run one customer turn through the engine.

    ``mode="shadow"`` (voice) runs the same pipeline but executes no action,
    writes no memory and makes no phrasing LLM call — it only logs.

    ``smart_script=True`` (Smart Script mode): the engine only decides the
    scenarios that end in an action or protect the customer; for all others it
    returns the scenario's guidance and leaves the wording to the org's script.
    """
    started = time.monotonic()
    live = mode == "live"
    llm = llm or _default_llm(api_key)
    timeout = settings.conversation_llm_timeout_seconds
    log_ctx: dict[str, Any] = {
        "org_id": org_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "channel": channel,
        "mode": mode,
        "text": text,
        "started": started,
    }
    understanding: Understanding | None = None
    decision: Decision | None = None
    tokens_in = tokens_out = 0

    async def fall_back(reason: str, **extra: Any) -> EngineResult:
        log_ctx["config"] = config
        logger.info("conversation_engine_fallback", reason=reason, org_id=str(org_id), mode=mode)
        await _log_turn(
            db,
            understanding=understanding,
            decision=decision,
            fallback_reason=reason,
            **log_ctx,
            **extra,
        )
        return EngineResult(None, reason, tokens_in, tokens_out)

    try:
        config = config or await resolve_config(db, org_id, conversation_id)
        log_ctx["config"] = config
        state = await _last_state(db, org_id, conversation_id)
        memory = await load_memory(db, org_id, user_id)

        try:
            understanding = await understand(
                text,
                state=state,
                history=history,
                memory=memory,
                llm=llm,
                timeout=timeout,
                config=config,
            )
        except UnderstandError as exc:
            return await fall_back(exc.reason)
        tokens_in += understanding.tokens_in
        tokens_out += understanding.tokens_out

        if (
            understanding.proposed_action is not None
            and understanding.proposed_action not in ALLOWED_ACTIONS
        ):
            return await fall_back("invalid_action", state=state)
        forced: Decision | None = None
        if understanding.confidence < settings.conversation_min_confidence:
            # Smart Script never improvises when unsure: the org's script answers.
            if not smart_script:
                forced = low_confidence_decision(
                    config.low_confidence_action, text=text, state=state
                )
            if forced is None:
                return await fall_back("low_confidence", state=state)

        if live and forced is None and understanding.scenario == "interested":
            await mark_lead_interested(db, org_id=org_id, user_id=user_id)

        if smart_script and understanding.scenario not in SMART_SCRIPT_ENGINE_SCENARIOS:
            # Smart Script: the org's script answers, steered by the scenario.
            if live:
                await _save_facts(
                    db, org_id, user_id, understanding, enhanced=config.enhanced_dialogue
                )
            guidance = scenario_guidance(config.get(understanding.scenario))
            await _log_turn(
                db,
                understanding=understanding,
                state=state,
                fallback_reason="smart_script",
                **log_ctx,
            )
            return EngineResult(None, "smart_script", tokens_in, tokens_out, guidance or None)

        knowledge: list[Any] = []
        if forced is None and wants_knowledge(understanding.scenario, config, understanding):
            knowledge = await find_knowledge(db, org_id, text, understanding.language)
        decision = forced or decide(
            understanding,
            text=text,
            memory=memory,
            knowledge=knowledge,
            channel=channel,
            config=config,
        )
        # Engine-internal, never model-proposed (so ALLOWED_ACTIONS stays as is): an
        # explicit "call me" with no time given means ring them now.
        call_now = (
            live
            and forced is None
            and wants_call_now(understanding, decision, text, channel)
        )
        if call_now:
            decision = Decision("initiate_ai_call", {}, "call_placed", next_state=STATE_ENGAGED)
        elif not validate_action(decision.action_id, decision.args):
            return await fall_back("invalid_action", state=state)

        action_result: dict[str, Any] | None = None
        if live and decision.action_id != "none":
            if dispatch is None:
                return await fall_back("error", state=state)
            action_result = await dispatch(decision.action_id, decision.args)
            if action_result.get("status") == "error":
                return await fall_back("action_failed", state=state, action_result=action_result)

        reply, phrase_in, phrase_out = await build_reply(
            decision,
            understanding,
            text=text,
            llm=llm if live else None,
            timeout=timeout,
            history=history,
            memory=memory,
            scenario_def=config.get(understanding.scenario),
        )
        tokens_in += phrase_in
        tokens_out += phrase_out

        if live:
            extra: dict[str, str] = {}
            forget: list[str] = []
            if config.enhanced_dialogue:
                extra, forget = dialogue.memory_updates(
                    understanding, decision, dialogue.DialogueState.from_memory(memory), memory
                )
            await _save_facts(
                db, org_id, user_id, understanding,
                extra=extra, forget=forget, enhanced=config.enhanced_dialogue,
            )

        await _log_turn(
            db,
            understanding=understanding,
            decision=decision,
            state=decision.next_state,
            knowledge_ids=[str(item.id) for item in decision.knowledge],
            action_result=action_result,
            reply=reply,
            **log_ctx,
        )
        if live and config.enhanced_dialogue:
            await record_disposition(
                db,
                org_id=org_id,
                user_id=user_id,
                understanding=understanding,
                decision=decision,
                action_result=action_result,
            )
        return EngineResult(reply, None, tokens_in, tokens_out)
    except Exception:  # noqa: BLE001 — engine trouble must never reach the customer
        logger.exception("conversation_engine_failed", org_id=str(org_id), mode=mode)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return await fall_back("error")
