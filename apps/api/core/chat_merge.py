"""Fold historical duplicate WhatsApp conversations into one per customer.

Before ``core/chat_threads.py`` every WhatsApp session older than 30 idle
minutes got its own ``conversations`` row. This moves everything that points at
those rows onto the customer's oldest WhatsApp row (the one
``get_or_create_whatsapp_conversation`` keeps using), then removes the emptied
duplicates. Voice rows are never touched — each call stays its own record.

Nothing is dropped: messages, engine turns, campaign targets and the lead's
transcript link are re-pointed, tags are unioned and AI summaries are
concatenated in order. Each customer is merged in its own transaction and
rolled back if the message count doesn't add up.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import CampaignTarget, Conversation, ConversationTurn, Lead, Message


@dataclass(frozen=True)
class MergeResult:
    org_id: UUID
    user_id: UUID
    survivor_id: UUID
    merged_conversations: int
    messages_moved: int


async def find_duplicate_groups(
    db: AsyncSession, org_id: UUID | None = None
) -> list[tuple[UUID, UUID, int]]:
    """(org_id, user_id, whatsapp conversation count) for every customer that has more than one."""
    stmt = (
        select(Conversation.org_id, Conversation.user_id, func.count(Conversation.id))
        .where(Conversation.channel == "whatsapp")
        .group_by(Conversation.org_id, Conversation.user_id)
        .having(func.count(Conversation.id) > 1)
        .order_by(func.count(Conversation.id).desc())
    )
    if org_id is not None:
        stmt = stmt.where(Conversation.org_id == org_id)
    return [(o, u, int(n)) for o, u, n in (await db.execute(stmt)).all()]


async def merge_customer(db: AsyncSession, org_id: UUID, user_id: UUID) -> MergeResult | None:
    """Merge one customer's WhatsApp rows. Caller commits (or rolls back on error)."""
    rows = (
        (
            await db.execute(
                select(Conversation)
                .where(
                    Conversation.org_id == org_id,
                    Conversation.user_id == user_id,
                    Conversation.channel == "whatsapp",
                )
                .order_by(Conversation.started_at.asc(), Conversation.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < 2:
        return None
    survivor, others = rows[0], rows[1:]
    other_ids = [c.id for c in others]
    all_ids = [survivor.id, *other_ids]

    before = (
        await db.execute(select(func.count(Message.id)).where(Message.conversation_id.in_(all_ids)))
    ).scalar_one()
    moving = (
        await db.execute(select(func.count(Message.id)).where(Message.conversation_id.in_(other_ids)))
    ).scalar_one()

    tags: list[str] = []
    summaries: list[str] = []
    for conv in rows:
        for tag in conv.tags or []:
            if tag not in tags:
                tags.append(tag)
        if conv.summary and conv.summary.strip():
            summaries.append(conv.summary.strip())

    await db.execute(
        update(Message).where(Message.conversation_id.in_(other_ids)).values(conversation_id=survivor.id)
    )
    await db.execute(
        update(ConversationTurn)
        .where(ConversationTurn.conversation_id.in_(other_ids))
        .values(conversation_id=survivor.id)
    )
    await db.execute(
        update(CampaignTarget)
        .where(CampaignTarget.conversation_id.in_(other_ids))
        .values(conversation_id=survivor.id)
    )
    await db.execute(
        update(Lead).where(Lead.conversation_id.in_(other_ids)).values(conversation_id=survivor.id)
    )

    survivor.tags = tags or None
    survivor.summary = "\n\n".join(summaries) or None
    survivor.ended_at = None

    # Guard rails before anything is deleted: every message is on the survivor,
    # none are left on a row we're about to drop.
    after = (
        await db.execute(select(func.count(Message.id)).where(Message.conversation_id == survivor.id))
    ).scalar_one()
    stranded = (
        await db.execute(select(func.count(Message.id)).where(Message.conversation_id.in_(other_ids)))
    ).scalar_one()
    if after != before or stranded:
        raise RuntimeError(
            f"merge check failed for user {user_id}: before={before} after={after} stranded={stranded}"
        )

    await db.execute(delete(Conversation).where(Conversation.id.in_(other_ids)))
    return MergeResult(org_id, user_id, survivor.id, len(others), int(moving))
