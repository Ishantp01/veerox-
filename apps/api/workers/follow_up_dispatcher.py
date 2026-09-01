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
   WhatsApp client for ``whatsapp``-channel leads, or places an outbound
   call (same one-off pattern as ``routers/admin.py``'s ``outbound_call``)
   for ``voice``-channel leads; a template-based rule always sends via
   WhatsApp regardless of the lead's channel (see ``_execute_task``). Any
   other channel has no automated send path and resolves to ``skipped``
   instead of silently never running.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from apps.api.channels.voice import failover as voice_failover
from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core.agent import _is_kill_switch_active
from apps.api.core.usage import get_credit_usage
from apps.api.db.models import FollowUpRule, FollowUpTask, Lead
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.deps import is_over_plan_limit
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 5
_BATCH_SIZE = 20

_DEFAULT_FOLLOW_UP_MESSAGE = "Following up as promised — is now still a good time to talk?"

# Veerox's orgs are India-based — matches workers/whatsapp_dispatcher.py's
# _SEND_TIME_ZONE, used the same way below to resolve "{{send_date}}"/
# "{{send_time}}" tokens fresh at send time.
_SEND_TIME_ZONE = ZoneInfo("Asia/Kolkata")


def _resolve_template_body_params(config: list[str] | None, lead_name: str | None) -> list[str]:
    """Turn a follow-up rule's configured per-placeholder tokens into the
    actual ``body_params`` sent to Meta, resolved fresh for this specific
    send — mirrors workers/whatsapp_dispatcher.py's identically-named
    function for campaigns, with the matched Lead's name standing in for a
    campaign's uploaded-file contact name.

    Recognized tokens: "{{contact_name}}" (this task's ``Lead.name``),
    "{{send_date}}"/"{{send_time}}" (IST, now). Anything else is used as a
    literal fixed value for that slot.
    """
    if not config:
        return []
    now_ist = datetime.now(_SEND_TIME_ZONE)
    resolved = []
    for token in config:
        if token == "{{contact_name}}":
            resolved.append(lead_name or "there")
        elif token == "{{send_date}}":
            resolved.append(now_ist.strftime("%d %b %Y"))
        elif token == "{{send_time}}":
            resolved.append(now_ist.strftime("%I:%M %p"))
        else:
            resolved.append(token)
    return resolved


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
            if not rule.template_name:
                # A template send reaches the recipient via WhatsApp
                # regardless of channel (see _execute_task), so only the
                # free-text/call path needs to match leads on the rule's
                # own channel — this was previously missing entirely, so a
                # rule created for one channel silently fired for every
                # lead matching the status, then got skipped at send time
                # for every lead on a different channel than the rule
                # actually intended.
                leads_stmt = leads_stmt.where(Lead.channel == rule.channel)
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
                                template_name=rule.template_name,
                                template_language=rule.template_language,
                                template_params=rule.template_params if rule.template_name else None,
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


async def _voice_call_permitted(org_id: UUID) -> bool:
    """True if this org is currently allowed to place an automated
    follow-up call — checked twice, a beat apart, on fresh connections
    before trusting a "no" answer.

    Defensive guard against a transient bad read: the exact same plan-limit
    check has been observed, live, to disagree with itself seconds apart
    against literally unchanged data (verified by re-running the identical
    query directly afterward and getting the correct answer every time —
    see the investigation notes for DELETE /follow-up-rules/{id}, which
    hit the same "reads correctly moments later" symptom against this
    database). That's a connection/pooling-layer issue, not application
    logic, so the fix here is resilience rather than a "correct" query —
    trusting a single read risked silently skipping calls that should have
    gone out.
    """
    for attempt in range(2):
        async with AsyncSessionLocal() as db:
            usage = await get_credit_usage(db, org_id)
            over_limit = await is_over_plan_limit(db, org_id, "max_call_minutes", usage.call_minutes)
        if not over_limit:
            return True
        if attempt == 0:
            await asyncio.sleep(1)
    return False


async def _place_follow_up_call(task_id: UUID, lead: Lead, org_record: Org | None) -> None:
    """Places an outbound voice call for a voice-channel lead's follow-up
    task — the same one-off single-call pattern as ``routers/admin.py``'s
    ``outbound_call`` (an org_id-scoped answer_url, no CallCampaign/
    CampaignTarget involved, since a follow-up call isn't part of a batch
    campaign — the org's default AI calling agent handles the conversation
    same as any admin-placed single call).

    Resolves to "sent" once the provider accepts the call request — that
    means the call was successfully *placed*, not that the recipient
    answered, same loose meaning "sent" already carries for a WhatsApp task.
    """
    if not voice_failover.is_configured():
        logger.warning("follow_up_dispatcher_voice_not_configured", task_id=str(task_id))
        await _resolve_task(task_id, "skipped")
        return

    plivo_from = (
        f"+{org_record.plivo_phone_number}" if org_record and org_record.plivo_phone_number else None
    )
    twilio_from = (
        f"+{org_record.twilio_phone_number}" if org_record and org_record.twilio_phone_number else None
    )
    answer_url = f"{settings.public_base_url.rstrip('/')}/voice/answer?org_id={lead.org_id}"

    try:
        _, provider = await voice_failover.initiate_call(
            lead.phone,
            answer_url,
            plivo_from_number=plivo_from,
            twilio_from_number=twilio_from,
            preferred_provider=org_record.preferred_voice_provider if org_record else None,
        )
        if provider == "twilio" and not twilio_from:
            logger.warning("follow_up_dispatcher_fell_back_to_twilio", task_id=str(task_id))
    except httpx.HTTPError:
        logger.warning("follow_up_dispatcher_call_failed", task_id=str(task_id))
        await _resolve_task(task_id, "failed")
        return

    await _resolve_task(task_id, "sent")


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

        org_record = await db.get(Org, lead.org_id)

        # Template sends (e.g. appointment reminders) reach the recipient via
        # Meta's pre-approved template path regardless of channel or open-
        # session state, so — unlike the free-form paths below — they skip
        # the channel gate entirely.
        message: str | None = None
        if task.template_name is None:
            if lead.channel == "voice":
                if not await _voice_call_permitted(lead.org_id):
                    # Same "don't place a call an over-limit org can't
                    # afford" gate campaign_dialer._claim_targets applies —
                    # skipped rather than retried, since this task is a
                    # one-shot (no requeue loop like campaign targets have).
                    # _voice_call_permitted already double-checked this
                    # before answering "no".
                    await _resolve_task(task_id, "skipped")
                    return
                await _place_follow_up_call(task_id, lead, org_record)
                return
            if lead.channel != "whatsapp":
                # No automated send path for any other/unknown channel —
                # surfaced to operators as "skipped" rather than staying
                # pending forever.
                await _resolve_task(task_id, "skipped")
                return
            rule = await db.get(FollowUpRule, task.rule_id) if task.rule_id else None
            message = rule.message_template if rule else (lead.follow_up_note or _DEFAULT_FOLLOW_UP_MESSAGE)

        # Send from this org's own dedicated WhatsApp number when it has one
        # (see Org.whatsapp_phone_number_id), falling back to the platform default.
        phone_number_id = org_record.whatsapp_phone_number_id if org_record else None

    try:
        if task.template_name:
            body_params = _resolve_template_body_params(task.template_params, lead.name)
            await wa_client.send_template(
                lead.phone,
                task.template_name,
                task.template_language or "en_US",
                body_params=body_params,
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
