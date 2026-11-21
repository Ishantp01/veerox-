"""Fold duplicate contacts (same org, same digits, ``+`` or not) into one.

The survivor is the contact with the most messages (oldest on a tie). Everything
that pointed at a duplicate is re-pointed at the survivor; the duplicate's
emptied row is then deleted. Where a uniqueness rule means both sides can't keep
their row (one lead per contact, one built-in follow-up per lead, one memory
fact per key, one read marker per staff member) the survivor's copy wins and the
other side's missing details are copied across first. Each person is merged in
one transaction by the caller and must keep every message and conversation.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import (
    Appointment,
    ChatRead,
    ChatThread,
    Contact,
    Conversation,
    ConversationMemory,
    ConversationTurn,
    FollowUpTask,
    Lead,
    Message,
    User,
)

_LEAD_FILL_FIELDS = (
    "contact_id", "conversation_id", "name", "phone", "intent", "channel", "follow_up_at",
    "follow_up_note", "follow_up_2_status", "follow_up_2_at", "follow_up_2_note",
    "follow_up_3_status", "follow_up_3_at", "follow_up_3_note", "qualification_score",
    "qualification_notes", "qualified_at", "deal_value", "claimed_by_account_user_id", "claimed_at",
)


@dataclass(frozen=True)
class UserMergeResult:
    org_id: UUID
    survivor_id: UUID
    merged_users: int
    messages: int
    conversations: int


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


async def find_duplicate_contacts(
    db: AsyncSession, org_id: UUID | None = None
) -> list[tuple[UUID, str, int]]:
    """(org_id, digits, row count) for every number stored as more than one contact."""
    stmt = select(User.org_id, User.id, User.phone)
    if org_id is not None:
        stmt = stmt.where(User.org_id == org_id)
    groups: dict[tuple[UUID, str], int] = defaultdict(int)
    for org, _, phone in (await db.execute(stmt)).all():
        digits = _digits(phone)
        if digits:
            groups[(org, digits)] += 1
    return sorted(
        ((org, digits, n) for (org, digits), n in groups.items() if n > 1), key=lambda g: -g[2]
    )


async def _count(db: AsyncSession, column, ids: list[UUID]) -> int:
    return int((await db.execute(select(func.count()).where(column.in_(ids)))).scalar_one())


async def merge_contact(db: AsyncSession, org_id: UUID, digits: str) -> UserMergeResult | None:
    """Merge one person's duplicate contacts. Caller commits, or rolls back on error."""
    users = list(
        (
            await db.execute(
                select(User)
                .where(User.org_id == org_id, User.phone.in_([digits, f"+{digits}"]))
                .order_by(User.created_at.asc(), User.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if len(users) < 2:
        return None

    message_counts = {
        u.id: await _count(db, Message.user_id, [u.id]) for u in users
    }
    survivor = max(users, key=lambda u: (message_counts[u.id], -users.index(u)))
    others = [u for u in users if u.id != survivor.id]
    other_ids = [u.id for u in others]
    all_ids = [survivor.id, *other_ids]

    before_messages = await _count(db, Message.user_id, all_ids)
    before_conversations = await _count(db, Conversation.user_id, all_ids)

    # Leads: one per contact. Keep the survivor's, fold the others' details and
    # dependents into it; if the survivor has none, simply adopt another's.
    keep_lead = (
        await db.execute(select(Lead).where(Lead.org_id == org_id, Lead.user_id == survivor.id))
    ).scalars().first()
    other_leads = (
        await db.execute(select(Lead).where(Lead.org_id == org_id, Lead.user_id.in_(other_ids)))
    ).scalars().all()
    for lead in sorted(other_leads, key=lambda l: l.created_at):
        if keep_lead is None:
            lead.user_id = survivor.id
            keep_lead = lead
            await db.flush()
            continue
        for field in _LEAD_FILL_FIELDS:
            if getattr(keep_lead, field) is None and getattr(lead, field) is not None:
                setattr(keep_lead, field, getattr(lead, field))
        if lead.tags:
            keep_lead.tags = list(dict.fromkeys([*(keep_lead.tags or []), *lead.tags]))
        keep_lead.created_at = min(keep_lead.created_at, lead.created_at)
        keep_lead.last_activity_at = max(keep_lead.last_activity_at, lead.last_activity_at)
        await db.execute(
            update(Appointment).where(Appointment.lead_id == lead.id).values(lead_id=keep_lead.id)
        )
        kept_tasks = (
            await db.execute(select(FollowUpTask).where(FollowUpTask.lead_id == keep_lead.id))
        ).scalars().all()
        kept_builtin = any(t.rule_id is None and t.template_name is None for t in kept_tasks)
        kept_rules = {t.rule_id for t in kept_tasks if t.rule_id is not None}
        for task in (
            await db.execute(select(FollowUpTask).where(FollowUpTask.lead_id == lead.id))
        ).scalars().all():
            clash = (
                (task.rule_id is None and task.template_name is None and kept_builtin)
                or (task.rule_id is not None and task.rule_id in kept_rules)
            )
            if clash:
                await db.delete(task)
            else:
                task.lead_id = keep_lead.id
        await db.flush()
        await db.delete(lead)
        await db.flush()

    await db.execute(
        update(Conversation).where(Conversation.user_id.in_(other_ids)).values(user_id=survivor.id)
    )
    await db.execute(update(Message).where(Message.user_id.in_(other_ids)).values(user_id=survivor.id))
    await db.execute(
        update(ConversationTurn).where(ConversationTurn.user_id.in_(other_ids)).values(user_id=survivor.id)
    )
    await db.execute(
        update(Contact).where(Contact.owner_user_id.in_(other_ids)).values(owner_user_id=survivor.id)
    )
    await db.execute(
        update(Appointment)
        .where(Appointment.assigned_user_id.in_(other_ids))
        .values(assigned_user_id=survivor.id)
    )

    # Threads: the person keeps one. Adopt another's if the survivor has none,
    # then hang every conversation off it and drop the emptied ones.
    keep_thread = (
        await db.execute(select(ChatThread).where(ChatThread.user_id == survivor.id))
    ).scalars().first()
    other_threads = (
        await db.execute(select(ChatThread).where(ChatThread.user_id.in_(other_ids)))
    ).scalars().all()
    if keep_thread is None and other_threads:
        keep_thread = other_threads[0]
        keep_thread.user_id = survivor.id
        await db.flush()
        other_threads = other_threads[1:]
    if keep_thread is not None:
        if other_threads:
            await db.execute(
                update(Conversation)
                .where(Conversation.chat_thread_id.in_([t.id for t in other_threads]))
                .values(chat_thread_id=keep_thread.id)
            )
            await db.execute(delete(ChatThread).where(ChatThread.id.in_([t.id for t in other_threads])))
        await db.execute(
            update(Conversation)
            .where(Conversation.user_id == survivor.id, Conversation.chat_thread_id.is_(None))
            .values(chat_thread_id=keep_thread.id)
        )

    # Memory facts: one per (scope, key) — the survivor's wins.
    have = {
        (m.scope, m.key)
        for m in (
            await db.execute(select(ConversationMemory).where(ConversationMemory.user_id == survivor.id))
        ).scalars()
    }
    for fact in (
        await db.execute(select(ConversationMemory).where(ConversationMemory.user_id.in_(other_ids)))
    ).scalars().all():
        if (fact.scope, fact.key) in have:
            await db.delete(fact)
        else:
            fact.user_id = survivor.id
            have.add((fact.scope, fact.key))

    # Read markers: one per staff member — keep the later read time.
    mine = {
        r.account_user_id: r
        for r in (await db.execute(select(ChatRead).where(ChatRead.user_id == survivor.id))).scalars()
    }
    for marker in (
        await db.execute(select(ChatRead).where(ChatRead.user_id.in_(other_ids)))
    ).scalars().all():
        existing = mine.get(marker.account_user_id)
        if existing is None:
            marker.user_id = survivor.id
            mine[marker.account_user_id] = marker
        else:
            existing.last_read_at = max(existing.last_read_at, marker.last_read_at)
            await db.delete(marker)
    await db.flush()

    if not survivor.name:
        survivor.name = next((u.name for u in others if u.name), None)

    after_messages = await _count(db, Message.user_id, [survivor.id])
    after_conversations = await _count(db, Conversation.user_id, [survivor.id])
    stranded = await _count(db, Message.user_id, other_ids) + await _count(
        db, Conversation.user_id, other_ids
    )
    if after_messages != before_messages or after_conversations != before_conversations or stranded:
        raise RuntimeError(
            f"contact merge check failed for ...{digits[-4:]}: messages {before_messages}->{after_messages}, "
            f"conversations {before_conversations}->{after_conversations}, stranded={stranded}"
        )

    await db.execute(delete(User).where(User.id.in_(other_ids)))
    return UserMergeResult(org_id, survivor.id, len(others), after_messages, after_conversations)
