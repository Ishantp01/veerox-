"""Admin endpoints for the Conversation Intelligence Engine.

* ``GET  /conversation-engine/status``      — this org's behaviour mode.
* ``GET  /conversation-engine/turns``       — the per-turn log (state, intent,
  scenario, confidence, knowledge used, action, final reply).
* ``/conversation-engine/knowledge``        — CRUD for approved knowledge.
* ``/conversation-engine/config``           — Scenario Builder: versions, draft,
  publish, rollback (``core/conversation/builder.py``).
* ``POST /conversation-engine/simulate``    — play the customer against any version.
* ``GET  /conversation-engine/audit``       — who changed what.
* ``GET  /conversation-engine/metrics``     — rollout metrics from the turn log.
* ``PATCH /conversation-engine/turns/{id}/review`` — mark a turn right / wrong.

Org admins only (plus ``X-Admin-Token`` / platform superusers): the turn log
holds every customer's messages, so per-member scoped sessions are refused.
Switching an org's mode is a platform-admin action via PATCH
``/billing/orgs/{id}`` (``conversation_mode``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.api.core.conversation import builder, voice_decision
from apps.api.db.models.conversation_engine import ConversationTurn, KnowledgeItem
from apps.api.db.models.org import CONVERSATION_MODES, Org
from apps.api.deps import (
    DbDep,
    MemberScopeDep,
    RequestOrgDep,
    require_feature,
    verify_admin_or_session,
)
from apps.api.routers.conversation_engine_config import ActorDep


def _require_org_manager(member_scope: MemberScopeDep) -> None:
    if member_scope is not None:
        raise HTTPException(status_code=403, detail="Insufficient role")


router = APIRouter(
    prefix="/conversation-engine",
    tags=["conversation-engine"],
    dependencies=[
        Depends(verify_admin_or_session),
        Depends(_require_org_manager),
        Depends(require_feature("conversation_engine")),
    ],
)

KnowledgeKind = Literal["product", "pricing", "policy", "faq"]
KnowledgeLanguage = Literal["hi", "en", "hinglish"]


class EngineStatusOut(BaseModel):
    conversation_mode: str
    voice_mode: str = "shadow"
    active_config_version: int | None = None
    has_draft: bool = False


class TurnOut(BaseModel):
    id: UUID
    conversation_id: UUID | None
    user_id: UUID | None
    channel: str
    mode: str
    user_text: str
    state: str | None
    intent: str | None
    scenario: str | None
    emotion: str | None
    language: str | None
    confidence: float | None
    source: str | None
    knowledge_ids: list[Any] | None
    action_id: str | None
    action_result: dict[str, Any] | None
    reply_text: str | None
    fallback_reason: str | None
    latency_ms: int | None
    config_version: int | None = None
    review_label: str | None = None
    review_scenario: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeIn(BaseModel):
    kind: KnowledgeKind
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)
    keywords: str | None = Field(default=None, max_length=1000)
    language: KnowledgeLanguage | None = None
    active: bool = True


class KnowledgeUpdateIn(BaseModel):
    kind: KnowledgeKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    keywords: str | None = Field(default=None, max_length=1000)
    language: KnowledgeLanguage | None = None
    active: bool | None = None


class KnowledgeOut(BaseModel):
    id: UUID
    kind: str
    title: str
    body: str
    keywords: str | None
    language: str | None
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/status", response_model=EngineStatusOut)
async def engine_status(db: DbDep, org_id: RequestOrgDep) -> EngineStatusOut:
    org = await db.get(Org, org_id)
    active = await builder.get_active_row(db, org_id)
    draft = await builder.get_draft(db, org_id)
    return EngineStatusOut(
        conversation_mode=(org.conversation_mode if org else None) or "strict_script",
        voice_mode=(org.conversation_voice_mode if org else None) or "shadow",
        active_config_version=active.version if active else None,
        has_draft=draft is not None,
    )


@router.get("/turns", response_model=list[TurnOut])
async def list_turns(
    db: DbDep,
    org_id: RequestOrgDep,
    conversation_id: UUID | None = Query(None),
    mode: Annotated[Literal["live", "shadow"] | None, Query()] = None,
    fallbacks_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConversationTurn]:
    stmt = select(ConversationTurn).where(ConversationTurn.org_id == org_id)
    if conversation_id is not None:
        stmt = stmt.where(ConversationTurn.conversation_id == conversation_id)
    if mode is not None:
        stmt = stmt.where(ConversationTurn.mode == mode)
    if fallbacks_only:
        stmt = stmt.where(ConversationTurn.fallback_reason.is_not(None))
    stmt = stmt.order_by(ConversationTurn.created_at.desc()).limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(db: DbDep, org_id: RequestOrgDep) -> list[KnowledgeItem]:
    stmt = (
        select(KnowledgeItem)
        .where(KnowledgeItem.org_id == org_id)
        .order_by(KnowledgeItem.kind, KnowledgeItem.title)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/knowledge", response_model=KnowledgeOut, status_code=201)
async def create_knowledge(
    payload: KnowledgeIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> KnowledgeItem:
    item = KnowledgeItem(org_id=org_id, **payload.model_dump())
    db.add(item)
    await db.flush()
    builder.add_audit(
        db, org_id, actor, "knowledge.created", "knowledge", str(item.id), {"title": item.title}
    )
    await db.commit()
    await db.refresh(item)
    return item


async def _get_owned(db: DbDep, org_id: UUID, item_id: UUID) -> KnowledgeItem:
    item = await db.get(KnowledgeItem, item_id)
    if item is None or item.org_id != org_id:
        raise HTTPException(status_code=404, detail="Knowledge item not found")
    return item


@router.patch("/knowledge/{item_id}", response_model=KnowledgeOut)
async def update_knowledge(
    item_id: UUID,
    payload: KnowledgeUpdateIn,
    db: DbDep,
    org_id: RequestOrgDep,
    actor: ActorDep,
) -> KnowledgeItem:
    item = await _get_owned(db, org_id, item_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None and key in ("kind", "title", "body", "active"):
            continue  # NOT NULL columns — null means "leave as is"
        setattr(item, key, value)
    builder.add_audit(
        db,
        org_id,
        actor,
        "knowledge.updated",
        "knowledge",
        str(item.id),
        {"title": item.title, "fields": sorted(payload.model_dump(exclude_unset=True))},
    )
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/knowledge/{item_id}", status_code=204)
async def delete_knowledge(
    item_id: UUID, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> None:
    item = await _get_owned(db, org_id, item_id)
    builder.add_audit(
        db, org_id, actor, "knowledge.deleted", "knowledge", str(item.id), {"title": item.title}
    )
    await db.delete(item)
    await db.commit()


class ReviewIn(BaseModel):
    label: Literal["correct", "incorrect"] | None
    correct_scenario: str | None = Field(default=None, max_length=32)


@router.patch("/turns/{turn_id}/review", response_model=TurnOut)
async def review_turn(
    turn_id: UUID, payload: ReviewIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> ConversationTurn:
    """Mark what the engine decided as right or wrong (Shadow Mode review)."""
    turn = await db.get(ConversationTurn, turn_id)
    if turn is None or turn.org_id != org_id:
        raise HTTPException(status_code=404, detail="Turn not found")
    turn.review_label = payload.label
    turn.review_scenario = payload.correct_scenario if payload.label == "incorrect" else None
    turn.reviewed_by = actor if payload.label else None
    turn.reviewed_at = datetime.now(UTC) if payload.label else None
    await db.commit()
    await db.refresh(turn)
    return turn


class ModeIn(BaseModel):
    mode: Literal["strict_script", "smart_script", "human_conversation"]


@router.put("/mode", response_model=EngineStatusOut)
async def set_mode(
    payload: ModeIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> EngineStatusOut:
    """The org's own admin switches how the AI behaves for this org.

    Strict Script is the instant rollback. Live voice stays a platform-admin
    setting (Organizations -> Edit), because it needs a tested phone line."""
    assert payload.mode in CONVERSATION_MODES
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org.conversation_mode != payload.mode:
        builder.add_audit(
            db,
            org_id,
            actor,
            "org.conversation_mode_changed",
            "org",
            str(org_id),
            {"from": org.conversation_mode, "to": payload.mode},
        )
        org.conversation_mode = payload.mode
        await db.commit()
        voice_decision.forget_org(org_id)
    return await engine_status(db, org_id)
