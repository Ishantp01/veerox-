"""Shared helpers for promoting/completing campaigns.

Called from both campaign_dialer.py and whatsapp_dispatcher.py's claim loops
(each already ticks every 5s) rather than running as its own poll loop — a
plain UPDATE...WHERE is idempotent, so both callers racing on the same tick
is harmless.
"""

from __future__ import annotations

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget


async def promote_scheduled_campaigns(db: AsyncSession) -> None:
    """Flip CallCampaign.status 'scheduled' -> 'running' once
    scheduled_start_at has passed."""
    await db.execute(
        update(CallCampaign)
        .where(CallCampaign.status == "scheduled", CallCampaign.scheduled_start_at <= func.now())
        .values(status="running")
    )
    await db.commit()


async def complete_finished_campaigns(db: AsyncSession) -> None:
    """Flip CallCampaign.status 'running' -> 'completed' once none of its
    targets are still 'pending'/'calling' — i.e. every target that was ever
    queued has reached a terminal 'completed'/'failed' status.

    Without this, a campaign that has fully finished dispatching stays
    'running' forever in the UI, indistinguishable from one still in
    progress. Requires at least one target row (a 'running' campaign always
    has one by the time it's queryable — targets are inserted in the same
    transaction that sets the initial status) so a pathological zero-target
    campaign doesn't instantly flip to 'completed'.
    """
    still_active = (
        select(CampaignTarget.campaign_id)
        .where(CampaignTarget.status.in_(("pending", "calling")))
        .where(CampaignTarget.campaign_id == CallCampaign.id)
    )
    has_targets = select(CampaignTarget.id).where(CampaignTarget.campaign_id == CallCampaign.id)
    await db.execute(
        update(CallCampaign)
        .where(
            CallCampaign.status == "running",
            ~exists(still_active),
            exists(has_targets),
        )
        .values(status="completed")
    )
    await db.commit()
