from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from apps.api.core.tools import _default_org_id
from apps.api.db.models import FollowUpRule, FollowUpTask
from apps.api.deps import DbDep, enforce_plan_feature, verify_admin_or_session
from apps.api.schemas.follow_up import (
    FollowUpRuleCreate,
    FollowUpRuleOut,
    FollowUpRuleUpdateIn,
    FollowUpTaskOut,
)

router = APIRouter(tags=["follow-ups"], dependencies=[Depends(verify_admin_or_session)])


@router.get("/follow-up-rules", response_model=list[FollowUpRuleOut])
async def list_follow_up_rules(db: DbDep) -> list[FollowUpRule]:
    org_id = _default_org_id()
    await enforce_plan_feature(db, org_id, "automated_followups")
    stmt = (
        select(FollowUpRule)
        .where(FollowUpRule.org_id == org_id)
        .order_by(FollowUpRule.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/follow-up-rules", response_model=FollowUpRuleOut, status_code=201)
async def create_follow_up_rule(payload: FollowUpRuleCreate, db: DbDep) -> FollowUpRule:
    org_id = _default_org_id()
    await enforce_plan_feature(db, org_id, "automated_followups")
    rule = FollowUpRule(
        org_id=org_id,
        name=payload.name,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        channel=payload.channel,
        message_template=payload.message_template,
        template_name=payload.template_name,
        template_language=payload.template_language,
        template_params=payload.template_params,
        active=payload.active,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.patch("/follow-up-rules/{rule_id}", response_model=FollowUpRuleOut)
async def update_follow_up_rule(rule_id: UUID, payload: FollowUpRuleUpdateIn, db: DbDep) -> FollowUpRule:
    await enforce_plan_feature(db, _default_org_id(), "automated_followups")
    rule = await db.get(FollowUpRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Follow-up rule not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/follow-up-rules/{rule_id}")
async def delete_follow_up_rule(rule_id: UUID, db: DbDep) -> dict[str, bool]:
    """Delete a rule. Tasks it already spawned (``FollowUpTask.rule_id``,
    ``ondelete="SET NULL"``) are kept for history — they just lose the rule
    link, matching how deleting a Contact keeps its Leads (routers/crm.py)."""
    await enforce_plan_feature(db, _default_org_id(), "automated_followups")
    rule = await db.get(FollowUpRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Follow-up rule not found")
    await db.delete(rule)
    await db.commit()
    return {"ok": True}


@router.get("/follow-up-tasks", response_model=list[FollowUpTaskOut])
async def list_follow_up_tasks(
    db: DbDep,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[FollowUpTask]:
    org_id = _default_org_id()
    await enforce_plan_feature(db, org_id, "automated_followups")
    stmt = (
        select(FollowUpTask)
        .where(FollowUpTask.org_id == org_id)
        .order_by(FollowUpTask.run_at.desc())
    )
    if status:
        stmt = stmt.where(FollowUpTask.status == status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/follow-up-tasks/{task_id}/cancel", response_model=FollowUpTaskOut)
async def cancel_follow_up_task(task_id: UUID, db: DbDep) -> FollowUpTask:
    await enforce_plan_feature(db, _default_org_id(), "automated_followups")
    task = await db.get(FollowUpTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Follow-up task not found")
    if task.status == "pending":
        task.status = "cancelled"
        await db.commit()
        await db.refresh(task)
    return task
