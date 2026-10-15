"""The Conversation Intelligence Engine — orchestrates processes 1.0–6.0.

``run_engine`` never raises: any problem becomes an ``EngineResult`` with
``reply=None`` and a ``fallback_reason``, which tells AgentCore to use the
legacy script/tool-calling path instead. Every run (answered or fallen back,
live or shadow) writes one ``ConversationTurn`` row.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation.decide import decide, validate_action
from apps.api.core.conversation.knowledge import find_knowledge
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.reply import build_reply
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    MEMORY_KEYS,
    Decision,
    EngineResult,
    Understanding,
)
from apps.api.core.conversation.understand import LLMCall, UnderstandError, understand
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
    }
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
) -> None:
    """Best-effort: a logging failure must never break the customer's turn."""
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
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("conversation_turn_log_failed", exc_info=True)
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
) -> EngineResult:
    """Run one customer turn through the engine.

    ``mode="shadow"`` (voice) runs the same pipeline but executes no action,
    writes no memory and makes no phrasing LLM call — it only logs.
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
        state = await _last_state(db, org_id, conversation_id)
        memory = await load_memory(db, org_id, user_id)

        try:
            understanding = await understand(
                text, state=state, history=history, memory=memory, llm=llm, timeout=timeout
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
        if understanding.confidence < settings.conversation_min_confidence:
            return await fall_back("low_confidence", state=state)

        knowledge: list[Any] = []
        if understanding.scenario in _KNOWLEDGE_SCENARIOS:
            knowledge = await find_knowledge(db, org_id, text, understanding.language)
        decision = decide(
            understanding, text=text, memory=memory, knowledge=knowledge, channel=channel
        )
        if not validate_action(decision.action_id, decision.args):
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
        )
        tokens_in += phrase_in
        tokens_out += phrase_out

        if live:
            facts = {k: v for k, v in understanding.facts.items() if k in MEMORY_KEYS}
            try:
                await save_facts(db, org_id, user_id, facts)
                await db.commit()
            except Exception:  # noqa: BLE001 — memory is best effort
                logger.warning("conversation_memory_save_failed", exc_info=True)
                await db.rollback()

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
        return EngineResult(reply, None, tokens_in, tokens_out)
    except Exception:  # noqa: BLE001 — engine trouble must never reach the customer
        logger.exception("conversation_engine_failed", org_id=str(org_id), mode=mode)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return await fall_back("error")
