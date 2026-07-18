"""In-process background dialer for outbound calling campaigns.

Started as an ``asyncio.create_task`` from the FastAPI lifespan (see
``apps/api/main.py``) rather than a separate job-queue process — throughput
is bounded by ``settings.max_concurrent_calls`` in-flight calls at a time
(the Plivo account's own concurrent-call cap is the ceiling on that number),
not by queue overhead, so a simple poll loop is enough. Durability across
restarts is handled by requeuing any target stuck ``calling`` on startup
rather than by a durable job broker.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select, update

from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.config import settings
from apps.api.core.agent import _is_kill_switch_active
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 5
_MAX_ATTEMPTS = 3
# Backstop only — Plivo's hangup_url callback (handle_call_ended, below) is
# what normally releases a finished call within seconds. This timeout only
# matters if that callback never arrives (network hiccup, Plivo outage).
# Generous enough not to cut off a real, still-in-progress conversation.
_STALE_CALL_TIMEOUT_SECS = 300


async def _requeue_stuck_targets() -> None:
    """Requeue targets left ``calling`` by a previous process (crash/restart
    mid-call) so a campaign never stalls forever on one dropped call.

    Scoped to voice campaigns only — ``CampaignTarget.status`` values are
    shared with the WhatsApp dispatcher (apps/api/workers/
    whatsapp_dispatcher.py), which has its own requeue-on-startup pass.
    """
    async with AsyncSessionLocal() as db:
        voice_target_ids = select(CampaignTarget.id).join(
            CallCampaign, CallCampaign.id == CampaignTarget.campaign_id
        ).where(CampaignTarget.status == "calling", CallCampaign.channel == "voice")
        stmt = (
            update(CampaignTarget)
            .where(CampaignTarget.id.in_(voice_target_ids))
            .values(status="pending")
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount:
            logger.info("campaign_dialer_requeued_stuck_targets", count=result.rowcount)


async def _reclaim_stale_calls(db) -> None:
    """Requeue/fail any voice target that's been ``calling`` past the timeout.

    Runs every tick so a bad number or a call that never connects doesn't
    permanently wedge the sequential dialer — no restart required. Scoped to
    voice campaigns; see ``_requeue_stuck_targets`` for why.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=_STALE_CALL_TIMEOUT_SECS)
    stmt = (
        select(CampaignTarget)
        .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
        .where(
            CampaignTarget.status == "calling",
            CampaignTarget.called_at < cutoff,
            CallCampaign.channel == "voice",
        )
    )
    stale = (await db.execute(stmt)).scalars().all()
    for target in stale:
        if target.conversation_id is not None:
            target.status = "failed"
        else:
            target.status = "pending" if target.attempt_count < _MAX_ATTEMPTS else "failed"
        logger.warning(
            "campaign_dialer_reclaimed_stale_call",
            target_id=str(target.id),
            new_status=target.status,
        )
    if stale:
        await db.commit()


async def _count_calls_in_flight(db) -> int:
    """How many voice calls are currently in progress — bounds concurrency.

    Scoped to voice campaigns so in-progress WhatsApp conversations (which
    share the "calling" status value) never count against the phone dialer.
    """
    stmt = (
        select(func.count())
        .select_from(CampaignTarget)
        .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
        .where(CampaignTarget.status == "calling", CallCampaign.channel == "voice")
    )
    return (await db.execute(stmt)).scalar_one()


async def _claim_targets() -> list[tuple[str, str, int]]:
    """Atomically claim up to the remaining concurrency budget's worth of the
    oldest pending targets of running campaigns.

    Returns ``[(target_id, phone, attempt_count), ...]`` as strings/int so
    the caller can place the calls outside this short-lived session. Empty
    if there's nothing to dial right now or ``max_concurrent_calls`` voice
    calls are already in flight.
    """
    async with AsyncSessionLocal() as db:
        await _reclaim_stale_calls(db)
        capacity = settings.max_concurrent_calls - await _count_calls_in_flight(db)
        if capacity <= 0:
            return []

        stmt = (
            select(CampaignTarget)
            .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
            .where(
                CampaignTarget.status == "pending",
                CallCampaign.status == "running",
                CallCampaign.channel == "voice",
            )
            .order_by(CampaignTarget.created_at)
            .limit(capacity)
        )
        targets = (await db.execute(stmt)).scalars().all()
        claimed = []
        for target in targets:
            target.status = "calling"
            target.attempt_count += 1
            target.called_at = datetime.now(UTC)
            claimed.append((str(target.id), target.phone, target.attempt_count))
        if claimed:
            await db.commit()
        return claimed


async def _mark_target(target_id: str, status: str) -> None:
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, target_id)
        if target is not None:
            target.status = status
            await db.commit()


async def handle_call_ended(target_id: str) -> None:
    """Free up a concurrency slot as soon as Plivo reports a call is over.

    Wired as the ``hangup_url`` on the outbound call (see ``_dial_batch``) so
    a no-answer/busy/dropped call frees up the dialer within seconds instead
    of waiting on the ``_STALE_CALL_TIMEOUT_SECS`` backstop. No-ops if the
    call already completed via ``qualify_lead`` (status is no longer
    ``"calling"`` by the time this fires).

    Only retries (``pending``) when the call never connected at all — a
    ``conversation_id`` on the target (set by ``voice_adapter.
    attach_campaign_conversation`` the moment the audio bridge connects) is
    proof the prospect actually answered and talked, so that always resolves
    to ``failed`` instead: re-dialing someone who already picked up just
    because the AI didn't call qualify_lead in time would be wrong, no
    matter how many attempts are left.
    """
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, UUID(target_id))
        if target is None or target.status != "calling":
            return
        if target.conversation_id is not None:
            target.status = "failed"
        else:
            target.status = "pending" if target.attempt_count < _MAX_ATTEMPTS else "failed"
        await db.commit()
        logger.info("campaign_dialer_call_ended", target_id=target_id, new_status=target.status)


async def _dial_one(target_id: str, phone: str, attempt_count: int) -> None:
    answer_url = (
        f"{settings.public_base_url.rstrip('/')}/voice/answer?campaign_target_id={target_id}"
    )
    hangup_url = (
        f"{settings.public_base_url.rstrip('/')}/voice/campaign-hangup"
        f"?campaign_target_id={target_id}"
    )

    if not voice_plivo.is_configured():
        # Local-dev fallback, same convention as POST /admin/outbound/call:
        # leave the target "calling" (simulating a placed call) rather than
        # failing it outright, so the dialer's concurrency gate and the
        # stuck-target requeue-on-restart path stay testable without real
        # Plivo credentials.
        logger.warning("campaign_dialer_plivo_not_configured", target_id=target_id)
        return

    try:
        await voice_plivo.initiate_call(phone, answer_url, hangup_url=hangup_url)
    except httpx.HTTPError:
        logger.warning("campaign_dialer_initiate_call_failed", target_id=target_id)
        await _mark_target(target_id, "pending" if attempt_count < _MAX_ATTEMPTS else "failed")


async def _dial_batch() -> None:
    claimed = await _claim_targets()
    if not claimed:
        return
    await asyncio.gather(
        *(_dial_one(target_id, phone, attempt_count) for target_id, phone, attempt_count in claimed)
    )


async def run_campaign_dialer() -> None:
    """The dialer's main loop — runs for the lifetime of the app process."""
    await _requeue_stuck_targets()
    while True:
        try:
            if not await _is_kill_switch_active():
                await _dial_batch()
        except Exception:  # noqa: BLE001
            logger.exception("campaign_dialer_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
