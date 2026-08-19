"""In-process background dispatcher for automated lead follow-ups.

Mirrors ``workers/campaign_dialer.py``'s poll-loop-in-lifespan structure.
Each tick does three things:

1. Materialize a ``FollowUpTask`` (``rule_id`` null) for every ``Lead`` whose
   own ``follow_up_at`` has arrived — the built-in trigger every lead already
   supports without configuring a rule.
2. Materialize a ``FollowUpTask`` per active ``FollowUpRule`` match — today
   only ``trigger_type="status_change"``, one task per (lead, rule) pair so a
   rule never re-fires on the same lead.
3. Execute due (``run_at`` in the past) ``pending`` tasks — sends via the
   WhatsApp client for ``whatsapp``-channel leads; other channels have no
   automated send path yet and resolve to ``skipped`` instead of silently
   never running.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from apps.api.channels.whatsapp import client as wa_client
from apps.api.core.agent import _is_kill_switch_active
from apps.api.db.models import FollowUpRule, FollowUpTask, Lead
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 5
_BATCH_SIZE = 20

_DEFAULT_FOLLOW_UP_MESSAGE = "Following up as promised — is now still a good time to talk?"


async def _materialize_lead_follow_up_at_tasks() -> None:
    """One task per lead whose own ``follow_up_at`` has arrived, skipping
    leads that already have a built-in (``rule_id`` is null) task.

    ``template_name IS NULL`` matters in both the "already exists" check
    below and the DB-level uniqueness this relies on (migration
    e7b3a5c9f2d4): core/tools.py's book_appointment also creates
    ``rule_id=NULL`` tasks (appointment reminders), several per lead, by
    design. Without this filter, a lead with appointment reminders would
    look like it "already has" its built-in follow-up task and silently
    never get one materialized.

    Duplicate-key errors (two dispatcher instances racing under horizontal
    scaling — see longrunning/operations/load.md) are caught per-row via a
    savepoint so one collision doesn't lose the rest of the batch; a
    single-instance deployment never hits this branch.
    """
    async with AsyncSessionLocal() as db:
        existing_lead_ids_stmt = select(FollowUpTask.lead_id).where(
            FollowUpTask.rule_id.is_(None), FollowUpTask.template_name.is_(None)
        )
        existing_lead_ids = set((await db.execute(existing_lead_ids_stmt)).scalars().all())

        stmt = select(Lead).where(Lead.follow_up_at.is_not(None), Lead.follow_up_at <= datetime.now(UTC))
        leads = (await db.execute(stmt)).scalars().all()

        created = 0
        for lead in leads:
            if lead.id in existing_lead_ids:
                continue
            try:
                async with db.begin_nested():
                    db.add(
                        FollowUpTask(
                            org_id=lead.org_id,
                            lead_id=lead.id,
                            rule_id=None,
                            run_at=lead.follow_up_at,
                            status="pending",
                        )
                    )
                    await db.flush()
            except IntegrityError:
                continue
            created += 1
        if created:
            await db.commit()
            logger.info("follow_up_dispatcher_materialized_lead_tasks", count=created)


async def _materialize_rule_tasks() -> None:
    """One task per (lead, rule) match for every active rule, skipping pairs
    that already have a task so a rule only ever fires once per lead.

    Duplicate-key errors (two dispatcher instances racing under horizontal
    scaling — see longrunning/operations/load.md) are caught per-row via a
    savepoint, matching _materialize_lead_follow_up_at_tasks above; a
    single-instance deployment never hits this branch.
    """
    async with AsyncSessionLocal() as db:
        rules = (
            await db.execute(select(FollowUpRule).where(FollowUpRule.active.is_(True)))
        ).scalars().all()

        created = 0
        for rule in rules:
            if rule.trigger_type != "status_change":
                continue
            target_status = rule.trigger_config.get("status")
            if not target_status:
                continue
            delay_hours = float(rule.trigger_config.get("delay_hours", 0))

            existing_lead_ids_stmt = select(FollowUpTask.lead_id).where(FollowUpTask.rule_id == rule.id)
            existing_lead_ids = set((await db.execute(existing_lead_ids_stmt)).scalars().all())

            leads_stmt = select(Lead).where(Lead.org_id == rule.org_id, Lead.status == target_status)
            leads = (await db.execute(leads_stmt)).scalars().all()

            for lead in leads:
                if lead.id in existing_lead_ids:
                    continue
                try:
                    async with db.begin_nested():
                        db.add(
                            FollowUpTask(
                                org_id=lead.org_id,
                                lead_id=lead.id,
                                rule_id=rule.id,
                                run_at=datetime.now(UTC) + timedelta(hours=delay_hours),
                                status="pending",
                            )
                        )
                        await db.flush()
                except IntegrityError:
                    continue
                created += 1
        if created:
            await db.commit()
            logger.info("follow_up_dispatcher_materialized_rule_tasks", count=created)


async def _claim_due_tasks() -> list[UUID]:
    """Atomically claim up to ``_BATCH_SIZE`` due pending tasks by flipping
    them to ``"sending"`` in the same transaction that selects them —
    mirrors ``campaign_dialer._claim_targets``'s claim-before-dispatch
    pattern so a second dispatcher tick (this process's next loop iteration,
    or another process/replica polling the same table) can never pick up a
    task this call already claimed.
    """
    async with AsyncSessionLocal() as db:
        stmt = (
            select(FollowUpTask.id)
            .where(FollowUpTask.status == "pending", FollowUpTask.run_at <= datetime.now(UTC))
            .order_by(FollowUpTask.run_at)
            .limit(_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        ids = list((await db.execute(stmt)).scalars().all())
        if not ids:
            return []
        await db.execute(
            update(FollowUpTask).where(FollowUpTask.id.in_(ids)).values(status="sending")
        )
        await db.commit()
        return ids


async def _resolve_task(task_id: UUID, status: str) -> None:
    async with AsyncSessionLocal() as db:
        task = await db.get(FollowUpTask, task_id)
        if task is not None:
            task.status = status
            if status == "sent":
                task.sent_at = datetime.now(UTC)
            await db.commit()


async def _execute_task(task_id: UUID) -> None:
    async with AsyncSessionLocal() as db:
        task = await db.get(FollowUpTask, task_id)
        if task is None or task.status != "sending":
            return
        lead = await db.get(Lead, task.lead_id)
        if lead is None:
            await _resolve_task(task_id, "failed")
            return

        if not lead.phone:
            await _resolve_task(task_id, "failed")
            return

        # Template sends (e.g. appointment reminders) reach the recipient via
        # Meta's pre-approved template path regardless of channel or open-
        # session state, so — unlike free-form text below — they skip the
        # whatsapp-channel gate.
        message: str | None = None
        if task.template_name is None:
            if lead.channel != "whatsapp":
                # No automated send path for voice (or unknown-channel) leads
                # yet — surfaced to operators as "skipped" rather than
                # staying pending forever.
                await _resolve_task(task_id, "skipped")
                return
            rule = await db.get(FollowUpRule, task.rule_id) if task.rule_id else None
            message = rule.message_template if rule else (lead.follow_up_note or _DEFAULT_FOLLOW_UP_MESSAGE)

        # Send from this org's own dedicated WhatsApp number when it has one
        # (see Org.whatsapp_phone_number_id), falling back to the platform default.
        org_record = await db.get(Org, lead.org_id)
        phone_number_id = org_record.whatsapp_phone_number_id if org_record else None

    try:
        if task.template_name:
            await wa_client.send_template(
                lead.phone,
                task.template_name,
                body_params=task.template_params or [],
                phone_number_id=phone_number_id,
            )
        else:
            await wa_client.send_text(lead.phone, message, phone_number_id=phone_number_id)
    except httpx.HTTPError:
        logger.warning("follow_up_dispatcher_send_failed", task_id=str(task_id))
        await _resolve_task(task_id, "failed")
        return

    await _resolve_task(task_id, "sent")


async def _requeue_stuck_tasks() -> None:
    """Requeue tasks left ``sending`` by a previous process (crash/restart
    mid-send) — mirrors ``campaign_dialer._requeue_stuck_targets``."""
    async with AsyncSessionLocal() as db:
        stmt = update(FollowUpTask).where(FollowUpTask.status == "sending").values(status="pending")
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount:
            logger.info("follow_up_dispatcher_requeued_stuck_tasks", count=result.rowcount)


async def _tick() -> None:
    await _materialize_lead_follow_up_at_tasks()
    await _materialize_rule_tasks()
    for task_id in await _claim_due_tasks():
        await _execute_task(task_id)


async def run_follow_up_dispatcher() -> None:
    """The dispatcher's main loop — runs for the lifetime of the app process."""
    await _requeue_stuck_tasks()
    while True:
        try:
            if not await _is_kill_switch_active():
                await _tick()
        except Exception:  # noqa: BLE001
            logger.exception("follow_up_dispatcher_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
