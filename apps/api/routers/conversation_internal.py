"""The conversation orchestrator's service boundary (blueprint section 22).

``POST /internal/conversation/turn``: the calling layer sends one customer turn
and gets back the next response plus *structured actions*. The orchestrator
decides; it does not execute. Actions come back as approved action ids with
validated arguments, and the caller runs them through the existing tools.

What it does persist: the structured memory updates and one turn-log row
(mode ``api``), so every decision is traceable. Tenant identity comes from the
authenticated server-side context only; a ``conversation_id`` that belongs to
another organisation is a 404.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.core.conversation.config import (
    EngineConfig,
    load_version_config,
    resolve_config,
)
from apps.api.core.conversation.engine import _last_state
from apps.api.core.conversation.memory import load_memory, save_facts
from apps.api.core.conversation.simulate import simulate_turn
from apps.api.core.conversation.types import MEMORY_KEYS
from apps.api.core.memory import load_last_n
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.db.models.conversation import Conversation
from apps.api.db.models.conversation_engine import ConversationConfigVersion, ConversationTurn
from apps.api.db.models.org import Org
from apps.api.deps import (
    DbDep,
    MemberScopeDep,
    RequestOrgDep,
    require_feature,
    verify_admin_or_session,
)


def _require_org_manager(member_scope: MemberScopeDep) -> None:
    if member_scope is not None:
        raise HTTPException(status_code=403, detail="Insufficient role")


router = APIRouter(
    prefix="/internal/conversation",
    tags=["conversation-internal"],
    dependencies=[
        Depends(verify_admin_or_session),
        Depends(_require_org_manager),
        Depends(require_feature("conversation_engine")),
    ],
)


class CallContext(BaseModel):
    channel: Literal["whatsapp", "voice"] = "whatsapp"
    # Optional recent messages; when absent the stored conversation history is used.
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class TurnIn(BaseModel):
    conversation_id: UUID
    # A published config version id. Omitted = the version this conversation started with.
    agent_version_id: UUID | None = None
    customer_transcript: str = Field(min_length=1, max_length=2000)
    call_context: CallContext = Field(default_factory=CallContext)
    timestamp: datetime | None = None


class ActionOut(BaseModel):
    id: str
    args: dict[str, Any]


class TurnOut(BaseModel):
    conversation_id: UUID
    agent_version_id: UUID | None
    agent_version: int
    response_text: str | None
    scenario: str | None
    state: str | None
    intent: str | None
    confidence: float | None
    memory_updates: dict[str, str]
    actions: list[ActionOut]
    # True when the caller must use the legacy script path for this turn.
    fallback_required: bool
    fallback_reason: str | None = None


@router.post("/turn", response_model=TurnOut)
async def conversation_turn(payload: TurnIn, db: DbDep, org_id: RequestOrgDep) -> TurnOut:
    conversation = await db.get(Conversation, payload.conversation_id)
    if conversation is None or conversation.org_id != org_id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    config: EngineConfig
    if payload.agent_version_id is not None:
        row = await db.get(ConversationConfigVersion, payload.agent_version_id)
        if row is None or row.org_id != org_id or row.status != "published":
            raise HTTPException(status_code=404, detail="Agent version not found")
        loaded = await load_version_config(db, org_id, payload.agent_version_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="Agent version not found")
        config = loaded
    else:
        config = await resolve_config(db, org_id, payload.conversation_id)

    def out(**kwargs: Any) -> TurnOut:
        return TurnOut(
            conversation_id=payload.conversation_id,
            agent_version_id=config.version_id,
            agent_version=config.version,
            **kwargs,
        )

    if org.conversation_mode == "strict_script":
        # The engine is switched off for this org: the script answers.
        return out(
            response_text=None, scenario=None, state=None, intent=None, confidence=None,
            memory_updates={}, actions=[], fallback_required=True, fallback_reason="engine_off",
        )

    user_id = conversation.user_id
    state = await _last_state(db, org_id, payload.conversation_id)
    memory = await load_memory(db, org_id, user_id)
    history = payload.call_context.history or await load_last_n(db, user_id, n=6)
    result = await simulate_turn(
        db,
        org_id,
        text=payload.customer_transcript,
        config=config,
        state=state,
        memory=memory,
        history=history,
        use_llm=True,
        api_key=resolve_openai_api_key(org),
        smart_script=org.conversation_mode == "smart_script",
        channel=payload.call_context.channel,
    )

    reply = result["reply"]
    fallback_reason = result["fallback_reason"]
    memory_updates: dict[str, str] = {}
    if reply is not None:
        memory_updates = {
            key: value
            for key, value in result["memory_after"].items()
            if key in MEMORY_KEYS and memory.get(key) != value
        }
        if memory_updates:
            await save_facts(db, org_id, user_id, memory_updates)
    actions: list[ActionOut] = []
    if reply is not None and result["action_id"] not in (None, "none"):
        actions.append(ActionOut(id=result["action_id"], args=result["action_args"] or {}))

    db.add(
        ConversationTurn(
            org_id=org_id,
            user_id=user_id,
            conversation_id=payload.conversation_id,
            channel=payload.call_context.channel,
            mode="api",
            user_text=payload.customer_transcript,
            state=result["state_after"] if reply is not None else state,
            intent=result["intent"],
            scenario=result["scenario"],
            emotion=result["emotion"],
            language=result["language"],
            confidence=result["confidence"],
            source=result["source"],
            knowledge_ids=[k["id"] for k in result["knowledge"]] or None,
            action_id=result["action_id"],
            reply_text=reply,
            fallback_reason=fallback_reason,
            latency_ms=result["latency_ms"],
            config_version_id=config.version_id,
            config_version=config.version,
        )
    )
    await db.commit()

    return out(
        response_text=reply,
        scenario=result["scenario"],
        state=result["state_after"] if reply is not None else state,
        intent=result["intent"],
        confidence=result["confidence"],
        memory_updates=memory_updates,
        actions=actions,
        fallback_required=reply is None,
        fallback_reason=fallback_reason,
    )

