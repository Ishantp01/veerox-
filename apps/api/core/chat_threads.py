"""One WhatsApp conversation per customer.

Every inbound/outbound WhatsApp message for a customer attaches to that
customer's single ``channel="whatsapp"`` conversation, however long the gap
since the last message. Voice calls are different on purpose: each call keeps
its own row because the recording webhook, retention sweep and campaign
bookkeeping all key on one row per Plivo call.

Per-session behaviour that used to come for free from "new row after 30 idle
minutes" (the conversation engine's state and pinned config) is now bounded by
``SESSION_IDLE_WINDOW`` inside the engine instead — see
``core/conversation/engine.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Conversation

# A WhatsApp "session" — the span over which the conversation engine keeps its
# state machine and pinned config — ends after this much idle time. Same value
# the old per-session conversation rows used.
SESSION_IDLE_WINDOW = timedelta(minutes=30)


def session_since(now: datetime | None = None) -> datetime:
    """Turns older than this belong to a previous WhatsApp session."""
    return (now or datetime.now(UTC)) - SESSION_IDLE_WINDOW


async def get_or_create_whatsapp_conversation(
    db: AsyncSession, org_id: UUID, user_id: UUID
) -> Conversation:
    """The customer's one WhatsApp conversation, created on first contact.

    If historical duplicates still exist (the merge script hasn't run yet) the
    oldest is used, which is the row the merge keeps as the survivor — so new
    messages never land on a row that is about to be folded away.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        # Serialise concurrent webhooks for the same customer so two first
        # messages can't each create a row. Transaction-scoped: released on
        # commit/rollback, no cleanup needed.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"wa-thread:{org_id}:{user_id}"},
        )
    existing = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.org_id == org_id,
                Conversation.user_id == user_id,
                Conversation.channel == "whatsapp",
            )
            .order_by(Conversation.started_at.asc(), Conversation.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # A reopened thread is live again.
        if existing.ended_at is not None:
            existing.ended_at = None
        return existing

    conversation = Conversation(org_id=org_id, user_id=user_id, channel="whatsapp")
    db.add(conversation)
    await db.flush()
    return conversation


def unified_whatsapp_enabled() -> bool:
    """Kill switch: UNIFIED_WHATSAPP_THREAD=false restores one row per session."""
    return bool(getattr(settings, "unified_whatsapp_thread", True))
