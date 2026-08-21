"""Shared helper for promoting scheduled campaigns to running.

Called from both campaign_dialer.py and whatsapp_dispatcher.py's claim loops
(each already ticks every 5s) rather than running as its own poll loop — a
plain UPDATE...WHERE is idempotent, so both callers racing on the same tick
is harmless.
"""

from __future__ import annotations

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.call_campaign import CallCampaign


async def promote_scheduled_campaigns(db: AsyncSession) -> None:
    """Flip CallCampaign.status 'scheduled' -> 'running' once
    scheduled_start_at has passed."""
    await db.execute(
        update(CallCampaign)
        .where(CallCampaign.status == "scheduled", CallCampaign.scheduled_start_at <= func.now())
        .values(status="running")
    )
    await db.commit()
