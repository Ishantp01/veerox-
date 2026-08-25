from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.db.models import FollowUpRule, FollowUpTask
from apps.api.deps import DbDep, RequestOrgDep, enforce_plan_feature, verify_admin_or_session
from apps.api.schemas.follow_up import (
    FollowUpRuleCreate,
    FollowUpRuleOut,
    FollowUpRuleUpdateIn,
    FollowUpTaskOut,
)

router = APIRouter(tags=["follow-ups"], dependencies=[Depends(verify_admin_or_session)])


@router.get("/follow-up-rules", response_model=list[FollowUpRuleOut])
async def list_follow_up_rules(db: DbDep, org: RequestOrgDep) -> list[FollowUpRule]:
    await enforce_plan_feature(db, org, "automated_followups")
    stmt = (
        select(FollowUpRule)
        .where(FollowUpRule.org_id == org)
        .order_by(FollowUpRule.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/follow-up-rules", response_model=FollowUpRuleOut, status_code=201)
async def create_follow_up_rule(
    payload: FollowUpRuleCreate, db: DbDep, org: RequestOrgDep
) -> FollowUpRule:
    await enforce_plan_feature(db, org, "automated_followups")
    rule = FollowUpRule(
        org_id=org,
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
async def update_follow_up_rule(
    rule_id: UUID, payload: FollowUpRuleUpdateIn, db: DbDep, org: RequestOrgDep
) -> FollowUpRule:
    await enforce_plan_feature(db, org, "automated_followups")
    rule = await db.get(FollowUpRule, rule_id)
    if rule is None or rule.org_id != org:
        raise HTTPException(status_code=404, detail="Follow-up rule not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/follow-up-rules/{rule_id}")
async def delete_follow_up_rule(rule_id: UUID, db: DbDep, org: RequestOrgDep) -> dict[str, bool]:
    """Delete a rule. Tasks it already spawned (``FollowUpTask.rule_id``,
    ``ondelete="SET NULL"``) are normally kept for history — they just lose
    the rule link, matching how deleting a Contact keeps its Leads
    (routers/crm.py).

    One exception: a free-text/call task (``template_name IS NULL``) whose
    lead ALREADY has its own independent builtin task (its own
    ``Lead.follow_up_at`` trigger, or a task orphaned by a previously
    deleted rule — also ``rule_id``/``template_name`` both null) can't be
    nulled the normal way — that would produce two ``rule_id IS NULL,
    template_name IS NULL`` rows for the same lead, violating
    ``uq_follow_up_tasks_lead_builtin`` (migration e7b3a5c9f2d4) and 500ing
    the whole delete. Rather than pre-checking for that conflict (racy: the
    background dispatcher's ``_materialize_lead_follow_up_at_tasks`` polls
    every 5s and can insert a fresh builtin task for any of these leads
    between a check and this endpoint's write), each task is nulled inside
    its own SAVEPOINT and, on a real collision, deleted instead — redundant
    once the rule's gone, since the lead's other builtin task already covers
    it. Same per-row savepoint-and-retry pattern
    workers/follow_up_dispatcher.py's materialize functions already use for
    the identical race between two dispatcher instances.
    """
    await enforce_plan_feature(db, org, "automated_followups")
    rule = await db.get(FollowUpRule, rule_id)
    if rule is None or rule.org_id != org:
        raise HTTPException(status_code=404, detail="Follow-up rule not found")

    rule_tasks_stmt = select(FollowUpTask).where(FollowUpTask.rule_id == rule_id)
    rule_tasks = (await db.execute(rule_tasks_stmt)).scalars().all()
    for task in rule_tasks:
        try:
            async with db.begin_nested():
                task.rule_id = None
                await db.flush()
        except IntegrityError:
            task.rule_id = rule_id
            async with db.begin_nested():
                await db.delete(task)
                await db.flush()

    await db.delete(rule)
    await db.commit()
    return {"ok": True}


@router.get("/follow-up-tasks", response_model=list[FollowUpTaskOut])
async def list_follow_up_tasks(
    db: DbDep,
    org: RequestOrgDep,
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[FollowUpTask]:
    await enforce_plan_feature(db, org, "automated_followups")
    stmt = (
        select(FollowUpTask)
        .where(FollowUpTask.org_id == org)
        .order_by(FollowUpTask.run_at.desc())
    )
    if status:
        stmt = stmt.where(FollowUpTask.status == status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("/follow-up-tasks/{task_id}/cancel", response_model=FollowUpTaskOut)
async def cancel_follow_up_task(task_id: UUID, db: DbDep, org: RequestOrgDep) -> FollowUpTask:
    await enforce_plan_feature(db, org, "automated_followups")
    task = await db.get(FollowUpTask, task_id)
    if task is None or task.org_id != org:
        raise HTTPException(status_code=404, detail="Follow-up task not found")
    if task.status == "pending":
        task.status = "cancelled"
        await db.commit()
        await db.refresh(task)
    return task
