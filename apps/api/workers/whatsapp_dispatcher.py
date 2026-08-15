"""In-process background dispatcher for outbound WhatsApp campaigns.

Mirrors ``workers/campaign_dialer.py``'s poll-loop-in-lifespan structure and
requeue-on-startup resilience, but a WhatsApp send is a single fire-and-forget
Graph API call rather than a call that stays "connected" for minutes — there's
no in-flight concurrency to bound the way ``max_concurrent_calls`` bounds the
dialer, so a target resolves to "completed" (sent) or "pending"/"failed"
within the same tick it's claimed, rather than staying "calling" until a
hangup callback arrives.

Qualification happens later, off this module's clock entirely: a prospect's
reply comes back through the normal inbound webhook
(``channels/whatsapp/adapter.py``), which resolves ``campaign_target_id`` for
any still-open target matching that phone and threads it through
``AgentCore.handle_turn`` so ``qualify_lead`` can record the verdict — the
same ``DISPATCH_TABLE`` machinery the voice realtime bridge already uses.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select, update

from apps.api.channels.whatsapp import client as wa_client
from apps.api.core.agent import _is_kill_switch_active
from apps.api.core.usage import get_credit_usage
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.deps import is_over_plan_limit
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 5
_MAX_ATTEMPTS = 3
# Messages sent per tick — there's no real concurrency ceiling for outbound
# Graph API calls the way Plivo caps simultaneous calls, so this just bounds
# how many targets one tick claims rather than gating in-flight capacity.
_BATCH_SIZE = 10


async def _requeue_stuck_targets() -> None:
    """Requeue WhatsApp targets left ``calling`` by a previous process
    (crash/restart mid-send). A send never legitimately stays ``calling`` for
    long, so anything found here on startup is a crash artifact.

    Scoped to WhatsApp campaigns only — ``CampaignTarget.status`` values are
    shared with the voice dialer (``workers/campaign_dialer.py``), which has
    its own requeue-on-startup pass.
    """
    async with AsyncSessionLocal() as db:
        wa_target_ids = select(CampaignTarget.id).join(
            CallCampaign, CallCampaign.id == CampaignTarget.campaign_id
        ).where(CampaignTarget.status == "calling", CallCampaign.channel == "whatsapp")
        stmt = (
            update(CampaignTarget)
            .where(CampaignTarget.id.in_(wa_target_ids))
            .values(status="pending")
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount:
            logger.info("whatsapp_dispatcher_requeued_stuck_targets", count=result.rowcount)


async def _claim_targets() -> list[tuple[str, str, str, int, str | None]]:
    """Atomically claim up to ``_BATCH_SIZE`` oldest pending targets of
    running WhatsApp campaigns.

    Returns ``[(target_id, phone, criteria, attempt_count, phone_number_id), ...]``
    so the caller can send outside this short-lived session.
    ``phone_number_id`` is the owning org's dedicated WhatsApp number (see
    ``Org.whatsapp_phone_number_id``), or ``None`` to fall back to the
    platform default.

    Targets belonging to an org that's over its plan's
    ``max_whatsapp_messages`` are skipped (left ``pending``, not
    claimed) — this is the only place campaign WhatsApp sends actually
    happen, so without this check a 0-message (or exhausted) plan would
    never stop an already-running campaign from sending.
    """
    async with AsyncSessionLocal() as db:
        # Over-fetch beyond `_BATCH_SIZE` since some candidates may belong to
        # an over-limit org and get skipped rather than claimed.
        stmt = (
            select(
                CampaignTarget, CallCampaign.criteria, CallCampaign.org_id, Org.whatsapp_phone_number_id
            )
            .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
            .join(Org, Org.id == CallCampaign.org_id)
            .where(
                CampaignTarget.status == "pending",
                CallCampaign.status == "running",
                CallCampaign.channel == "whatsapp",
            )
            .order_by(CampaignTarget.created_at)
            .limit(_BATCH_SIZE * 4)
        )
        rows = (await db.execute(stmt)).all()

        org_over_limit: dict[UUID, bool] = {}
        claimed = []
        for target, criteria, org_id, phone_number_id in rows:
            if len(claimed) >= _BATCH_SIZE:
                break
            if org_id not in org_over_limit:
                usage = await get_credit_usage(db, org_id)
                org_over_limit[org_id] = await is_over_plan_limit(
                    db, org_id, "max_whatsapp_messages", usage.whatsapp_messages
                )
            if org_over_limit[org_id]:
                continue
            target.status = "calling"
            target.attempt_count += 1
            target.called_at = datetime.now(UTC)
            claimed.append(
                (str(target.id), target.phone, criteria, target.attempt_count, phone_number_id)
            )
        if claimed:
            await db.commit()
        return claimed


async def _mark_target(target_id: str, status: str) -> None:
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, UUID(target_id))
        if target is not None:
            target.status = status
            await db.commit()


def _opening_message(criteria: str) -> str:
    """First outbound message for a campaign target.

    States the campaign's intent (``criteria``) plainly since there's no live
    agent voice to improvise an opener the way the realtime bridge does for
    calls — the prospect's reply is what starts the real AI conversation,
    handled the same as any other inbound WhatsApp message from there.
    """
    return (
        f"Hi! We're reaching out about the following: {criteria.strip()} "
        "Reply here anytime — happy to help."
    )


async def _send_one(
    target_id: str, phone: str, criteria: str, attempt_count: int, phone_number_id: str | None
) -> None:
    try:
        await wa_client.send_text(phone, _opening_message(criteria), phone_number_id=phone_number_id)
    except httpx.HTTPError:
        logger.warning("whatsapp_dispatcher_send_failed", target_id=target_id)
        await _mark_target(target_id, "pending" if attempt_count < _MAX_ATTEMPTS else "failed")
        return

    # A successful send IS the deliverable for this worker — there's no
    # "connects" event to wait for the way a phone call has one. Any reply
    # routes through the normal inbound webhook, which resolves this target
    # via channels/whatsapp/adapter.py's campaign-target lookup.
    await _mark_target(target_id, "completed")


async def _dispatch_batch() -> None:
    claimed = await _claim_targets()
    if not claimed:
        return
    await asyncio.gather(
        *(
            _send_one(target_id, phone, criteria, attempt_count, phone_number_id)
            for target_id, phone, criteria, attempt_count, phone_number_id in claimed
        )
    )


async def run_whatsapp_dispatcher() -> None:
    """The dispatcher's main loop — runs for the lifetime of the app process."""
    await _requeue_stuck_targets()
    while True:
        try:
            if not await _is_kill_switch_active():
                await _dispatch_batch()
        except Exception:  # noqa: BLE001
            logger.exception("whatsapp_dispatcher_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
