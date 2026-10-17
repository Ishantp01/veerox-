"""Conversation simulator (quotation 2.3): the admin plays the customer.

Runs the *same* understand -> decide -> reply pipeline as a live turn, against
any configuration (draft, active or an older version), but changes nothing: no
action is executed, no memory or turn row is written. The caller keeps the
simulated state and memory between turns and sends them back each time.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.conversation.config import (
    SMART_SCRIPT_ENGINE_SCENARIOS,
    EngineConfig,
    scenario_guidance,
)
from apps.api.core.conversation.decide import decide, low_confidence_decision, validate_action
from apps.api.core.conversation.engine import _default_llm, wants_knowledge
from apps.api.core.conversation.knowledge import find_knowledge
from apps.api.core.conversation.reply import build_reply
from apps.api.core.conversation.types import ALLOWED_ACTIONS, MEMORY_KEYS
from apps.api.core.conversation.understand import UnderstandError, understand
from apps.api.core.llm import ChatResult


async def _no_llm(messages: list[dict[str, Any]]) -> ChatResult:
    raise RuntimeError("model calls are switched off for this simulation")


async def simulate_turn(
    db: AsyncSession,
    org_id: UUID,
    *,
    text: str,
    config: EngineConfig,
    state: str | None,
    memory: dict[str, str],
    history: list[dict[str, Any]],
    use_llm: bool,
    api_key: str | None,
    smart_script: bool,
    channel: str = "whatsapp",
) -> dict[str, Any]:
    started = time.monotonic()
    llm = _default_llm(api_key) if use_llm else _no_llm
    timeout = settings.conversation_llm_timeout_seconds

    result: dict[str, Any] = {
        "config_version": config.version,
        "state_before": state,
        "scenario": None,
        "intent": None,
        "emotion": None,
        "language": None,
        "confidence": None,
        "source": None,
        "facts": {},
        "state_after": state,
        "memory_after": dict(memory),
        "action_id": None,
        "action_args": None,
        "would_run_action": False,
        "reply_kind": None,
        "reply": None,
        "knowledge": [],
        "guidance": None,
        "fallback_reason": None,
        "handled_by": "engine",
        "latency_ms": 0,
    }

    def finish() -> dict[str, Any]:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        return result

    try:
        understanding = await understand(
            text, state=state, history=history, memory=memory, llm=llm, timeout=timeout, config=config
        )
    except UnderstandError as exc:
        result["fallback_reason"] = exc.reason
        result["handled_by"] = "script"
        return finish()

    result.update(
        scenario=understanding.scenario,
        intent=understanding.intent,
        emotion=understanding.emotion,
        language=understanding.language,
        confidence=understanding.confidence,
        source=understanding.source,
        facts=understanding.facts,
    )
    if understanding.proposed_action is not None and understanding.proposed_action not in ALLOWED_ACTIONS:
        result.update(fallback_reason="invalid_action", handled_by="script")
        return finish()
    forced = None
    if understanding.confidence < settings.conversation_min_confidence:
        if not smart_script:
            forced = low_confidence_decision(config.low_confidence_action, text=text, state=state)
        if forced is None:
            result.update(fallback_reason="low_confidence", handled_by="script")
            return finish()

    memory_after = dict(memory)
    memory_after.update({k: v for k, v in understanding.facts.items() if k in MEMORY_KEYS})
    result["memory_after"] = memory_after

    if smart_script and understanding.scenario not in SMART_SCRIPT_ENGINE_SCENARIOS:
        result.update(
            fallback_reason="smart_script",
            handled_by="script (steered by scenario guidance)",
            guidance=scenario_guidance(config.get(understanding.scenario)) or None,
        )
        return finish()

    knowledge: list[Any] = []
    if forced is None and wants_knowledge(understanding.scenario, config):
        knowledge = await find_knowledge(db, org_id, text, understanding.language)
    decision = forced or decide(
        understanding, text=text, memory=memory, knowledge=knowledge, channel=channel, config=config
    )
    if not validate_action(decision.action_id, decision.args):
        result.update(fallback_reason="invalid_action", handled_by="script")
        return finish()

    reply, _, _ = await build_reply(
        decision,
        understanding,
        text=text,
        llm=llm if use_llm else None,
        timeout=timeout,
        history=history,
        memory=memory,
        scenario_def=config.get(understanding.scenario),
    )
    result.update(
        state_after=decision.next_state,
        action_id=decision.action_id,
        action_args=decision.args or None,
        would_run_action=decision.action_id != "none",
        reply_kind=decision.reply_kind,
        reply=reply,
        knowledge=[{"id": str(item.id), "title": item.title} for item in decision.knowledge],
        guidance=scenario_guidance(config.get(understanding.scenario)) or None,
    )
    return finish()
