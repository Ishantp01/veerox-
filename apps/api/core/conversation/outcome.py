"""CRM outcome of a conversation turn, using fields the Lead already has.

No new columns: the disposition is a single ``disposition:<name>`` entry in
``Lead.tags`` (an existing JSON list). The state names are the ones the sales
team needs to tell apart: interested, undecided, not_interested,
callback_requested, meeting_proposed, meeting_confirmed. Only a successful
``book_appointment`` can produce ``meeting_confirmed`` (the booking tool also
creates the Appointment row), and an undecided customer is never recorded as
confirmed. Only runs for orgs on the enhanced dialogue mode.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.dialogue import apply_disposition_to_tags, disposition_for
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.db.models.lead import Lead

logger = structlog.get_logger(__name__)


async def record_disposition(
    db: AsyncSession,
    *,
    org_id: UUID,
    user_id: UUID,
    understanding: Understanding,
    decision: Decision,
    action_result: dict[str, Any] | None,
) -> str | None:
    """Update the customer's lead tag; returns the stored disposition, or None
    when nothing changed. Org-scoped; never raises."""
    try:
        disposition = disposition_for(understanding, decision, action_result)
        if disposition is None:
            return None
        lead = (
            await db.execute(select(Lead).where(Lead.org_id == org_id, Lead.user_id == user_id))
        ).scalar_one_or_none()
        if lead is None:
            return None  # the engine never creates a lead just to tag it
        tags = apply_disposition_to_tags(lead.tags, disposition)
        if tags is None:
            return None
        lead.tags = tags
        await db.commit()
        return disposition
    except Exception:  # noqa: BLE001 - CRM tagging must never break a turn
        logger.warning("conversation_disposition_failed", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
