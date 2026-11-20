"""Unified Chat module: one thread per customer across WhatsApp and voice.

Read-model only. A customer (``users.id``) can own many ``conversations`` rows
— a voice call is one row per call, a WhatsApp session rolls over after the
open-conversation window — and the pipelines, recording webhook, campaigns and
the conversation engine all key on those rows, so they are deliberately left
alone. This router stitches every row (and every message) of one customer into
a single chronological thread instead of merging them, so no history is moved,
re-pointed or deleted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import (
    AccountUser,
    ChatRead,
    Contact,
    Conversation,
    Lead,
    Message,
    User,
    WhatsAppTemplate,
)
from apps.api.core.whatsapp_render import LEGACY_TEMPLATE_MARKER, render_template_message
from apps.api.deps import (
    RequestOrgDep,
    DbDep,
    MemberScopeDep,
    RequestAccountUserDep,
    owned_lead_user_ids,
    require_feature,
    verify_admin_or_session,
)

router = APIRouter(
    prefix="/admin/chat",
    tags=["chat"],
    dependencies=[Depends(verify_admin_or_session), Depends(require_feature("conversations"))],
)

# Messages that existed before the Chat module shipped are never "unread" —
# otherwise every customer would open with their whole history badged.
CHAT_UNREAD_EPOCH = datetime(2026, 11, 19, tzinfo=UTC)

# Roles that are plumbing for the LLM, not part of what the customer saw.
_HIDDEN_ROLES = ("system", "tool")

_PREVIEW_CHARS = 120


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[: _PREVIEW_CHARS - 1] + "…"


async def _legacy_template_text(db: AsyncSession, messages: list[Message]) -> dict[UUID, str]:
    """Rows saved before sends were stored as rendered text hold only a
    ``[template:name]`` marker. Show the saved template's text instead; the
    values it was filled with weren't kept, so any {{n}} stays visible."""
    wanted: dict[UUID, set[str]] = {}
    for m in messages:
        found = LEGACY_TEMPLATE_MARKER.match(m.content or "")
        if found:
            wanted.setdefault(m.org_id, set()).add(found.group(1))
    if not wanted:
        return {}
    templates = (
        await db.execute(
            select(WhatsAppTemplate).where(
                or_(
                    *[
                        and_(WhatsAppTemplate.org_id == org, WhatsAppTemplate.name.in_(names))
                        for org, names in wanted.items()
                    ]
                )
            )
        )
    ).scalars().all()
    by_key = {(t.org_id, t.name): t for t in templates}
    out: dict[UUID, str] = {}
    for m in messages:
        found = LEGACY_TEMPLATE_MARKER.match(m.content or "")
        template = by_key.get((m.org_id, found.group(1))) if found else None
        if template is not None and (template.body_preview or "").strip():
            out[m.id] = render_template_message(template, found.group(1))
    return out


def _aware(value: datetime | None) -> datetime | None:
    """SQLite (used by the test suite) hands back naive datetimes."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def _guard_customer(
    db: AsyncSession,
    user_id: UUID,
    scope_org_id: UUID | None,
    member_scope: UUID | None,
) -> User:
    """404 unless the caller may see this customer's thread. Same rules as
    ``admin._guard_conversation_access``: org-scoped callers only see their own
    org, and a ``role=="member"`` only sees customers behind a Lead they
    claimed. Always 404, never 403, so ids can't be probed."""
    user = await db.get(User, user_id)
    if user is None or (scope_org_id is not None and user.org_id != scope_org_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    if member_scope is not None:
        owned = await db.execute(
            owned_lead_user_ids(member_scope).where(Lead.user_id == user_id).limit(1)
        )
        if owned.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Chat not found")
    return user


@router.get("/leads")
async def list_chat_leads(
    db: DbDep,
    scope_org_id: RequestOrgDep,
    member_scope: MemberScopeDep,
    account_user_id: RequestAccountUserDep,
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
    search: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Chats of the org you are working in (never other orgs, even for a platform
    owner — the same number in two orgs is two separate customers).

    One row per lead/customer — anyone with a conversation or a Lead record,
    including imported leads nobody has messaged yet — most recently active
    first. ``channel`` keeps customers who have used that channel at least
    once (the thread itself always shows every channel)."""
    last_message_at = (
        select(func.max(Message.created_at))
        .where(Message.user_id == User.id, Message.role.notin_(_HIDDEN_ROLES))
        .correlate(User)
        .scalar_subquery()
    )
    conv_agg = (
        select(
            Conversation.user_id.label("user_id"),
            func.max(Conversation.started_at).label("last_started"),
            func.count(Conversation.id).label("conversation_count"),
        )
        .group_by(Conversation.user_id)
    )
    if channel:
        conv_agg = conv_agg.where(Conversation.channel == channel)
    conv_agg = conv_agg.subquery()

    lead_activity = (
        select(func.max(Lead.last_activity_at))
        .where(Lead.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    last_activity = func.coalesce(last_message_at, conv_agg.c.last_started, lead_activity)
    stmt = select(
        User,
        func.coalesce(conv_agg.c.conversation_count, 0),
        last_activity.label("last_activity"),
    ).outerjoin(conv_agg, conv_agg.c.user_id == User.id)
    if channel:
        # Leads with no conversation have no channel history to filter on.
        stmt = stmt.where(conv_agg.c.user_id.is_not(None))
    else:
        stmt = stmt.where(
            or_(
                conv_agg.c.user_id.is_not(None),
                User.id.in_(select(Lead.user_id)),
            )
        )
    if scope_org_id is not None:
        stmt = stmt.where(User.org_id == scope_org_id)
    if member_scope is not None:
        stmt = stmt.where(User.id.in_(owned_lead_user_ids(member_scope)))
    if search:
        pattern = f"%{search}%"
        digits = re.sub(r"\D", "", search)
        clauses = [User.name.ilike(pattern), User.phone.ilike(pattern)]
        if digits:
            clauses.append(User.phone.ilike(f"%{digits}%"))
        clauses.append(
            User.id.in_(
                select(Conversation.user_id).where(
                    cast(Conversation.tags, String).ilike(pattern)
                )
            )
        )
        stmt = stmt.where(or_(*clauses))
    stmt = stmt.order_by(last_activity.desc(), User.id).limit(limit).offset(offset)

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []
    user_ids = [user.id for user, _, _ in rows]

    # Latest visible message per customer (ties on created_at collapsed below).
    newest = (
        select(Message.user_id, func.max(Message.created_at).label("at"))
        .where(Message.user_id.in_(user_ids), Message.role.notin_(_HIDDEN_ROLES))
        .group_by(Message.user_id)
        .subquery()
    )
    last_rows = (
        await db.execute(
            select(Message)
            .join(
                newest,
                and_(Message.user_id == newest.c.user_id, Message.created_at == newest.c.at),
            )
            .where(Message.role.notin_(_HIDDEN_ROLES))
        )
    ).scalars()
    last_by_user: dict[UUID, Message] = {}
    for message in last_rows:
        last_by_user.setdefault(message.user_id, message)

    # Unread = inbound messages newer than this staff member's last read (or
    # the module's launch epoch if they've never opened the thread).
    unread_rows = await db.execute(
        select(Message.user_id, func.count(Message.id))
        .outerjoin(
            ChatRead,
            and_(ChatRead.user_id == Message.user_id, ChatRead.account_user_id == account_user_id),
        )
        .where(
            Message.user_id.in_(user_ids),
            Message.role == "user",
            Message.created_at > func.coalesce(ChatRead.last_read_at, CHAT_UNREAD_EPOCH),
        )
        .group_by(Message.user_id)
    )
    unread_by_user = {uid: int(n) for uid, n in unread_rows.all()}

    lead_rows = await db.execute(
        select(Lead.user_id, Lead.status, AccountUser.full_name)
        .outerjoin(AccountUser, AccountUser.id == Lead.claimed_by_account_user_id)
        .where(Lead.user_id.in_(user_ids))
    )
    lead_by_user = {uid: (status, staff) for uid, status, staff in lead_rows.all()}

    legacy_text = await _legacy_template_text(db, list(last_by_user.values()))
    result: list[dict[str, Any]] = []
    for user, conversation_count, activity in rows:
        last = last_by_user.get(user.id)
        status, staff = lead_by_user.get(user.id, (None, None))
        result.append(
            {
                "user_id": str(user.id),
                "name": user.name,
                "phone": user.phone,
                "last_message_preview": _preview(legacy_text.get(last.id, last.content)) if last else None,
                "last_message_role": last.role if last else None,
                "last_message_channel": last.channel if last else None,
                "last_activity_at": _aware(activity).isoformat() if activity else None,
                "unread_count": unread_by_user.get(user.id, 0),
                "conversation_count": int(conversation_count),
                "lead_status": status,
                "staff_name": staff,
            }
        )
    return result


@router.get("/leads/{user_id}")
async def get_chat_thread(
    user_id: UUID,
    db: DbDep,
    scope_org_id: RequestOrgDep,
    member_scope: MemberScopeDep,
    limit: int = Query(300, ge=1, le=1000),
    before: datetime | None = Query(
        None, description="Only messages strictly older than this — pages back through history."
    ),
) -> dict[str, Any]:
    """Contact details plus the whole thread, oldest → newest.

    ``messages`` is the newest ``limit`` messages (older ones via ``before``),
    already sorted ascending. ``conversations`` carries each underlying row's
    channel / call metadata / summary so the client can wrap a voice call's
    transcript lines in a call card; every message names its
    ``conversation_id``.
    """
    user = await _guard_customer(db, user_id, scope_org_id, member_scope)

    msg_stmt = select(Message).where(
        Message.user_id == user_id, Message.role.notin_(_HIDDEN_ROLES)
    )
    if before is not None:
        msg_stmt = msg_stmt.where(Message.created_at < before)
    newest_first = (
        await db.execute(
            msg_stmt.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
        )
    ).scalars().all()
    has_more = len(newest_first) > limit
    page = list(reversed(newest_first[:limit]))
    legacy_text = await _legacy_template_text(db, page)

    conversations = (
        await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.started_at.asc(), Conversation.id.asc())
        )
    ).scalars().all()

    lead_row = (
        await db.execute(
            select(Lead, AccountUser.full_name, Contact.email, Contact.company)
            .outerjoin(AccountUser, AccountUser.id == Lead.claimed_by_account_user_id)
            .outerjoin(Contact, Contact.id == Lead.contact_id)
            .where(Lead.user_id == user_id, Lead.org_id == user.org_id)
        )
    ).first()
    lead_payload: dict[str, Any] | None = None
    if lead_row is not None:
        lead, staff_name, email, company = lead_row
        lead_payload = {
            "id": str(lead.id),
            "name": lead.name,
            "status": lead.status,
            "intent": lead.intent,
            "tags": lead.tags or [],
            "qualification_status": lead.qualification_status,
            "follow_up_at": _aware(lead.follow_up_at).isoformat() if lead.follow_up_at else None,
            "created_at": _aware(lead.created_at).isoformat(),
            "staff_name": staff_name,
            "email": email,
            "company": company,
        }

    return {
        "contact": {
            "user_id": str(user.id),
            "name": user.name or (lead_payload or {}).get("name"),
            "phone": user.phone,
            "first_seen_at": _aware(user.created_at).isoformat(),
        },
        "lead": lead_payload,
        "conversations": [
            {
                "id": str(c.id),
                "channel": c.channel,
                "started_at": _aware(c.started_at).isoformat(),
                "ended_at": _aware(c.ended_at).isoformat() if c.ended_at else None,
                "recording_url": c.recording_url,
                "recording_duration_secs": c.recording_duration_secs,
                "summary": c.summary,
                "tags": c.tags or [],
            }
            for c in conversations
        ],
        "messages": [
            {
                "id": str(m.id),
                "conversation_id": str(m.conversation_id),
                "role": m.role,
                "channel": m.channel,
                "content": legacy_text.get(m.id, m.content),
                "audio_secs": m.audio_secs,
                "created_at": _aware(m.created_at).isoformat(),
            }
            for m in page
        ],
        "has_more": has_more,
        "next_before": _aware(page[0].created_at).isoformat() if page and has_more else None,
    }


@router.post("/leads/{user_id}/read", status_code=204)
async def mark_chat_read(
    user_id: UUID,
    db: DbDep,
    scope_org_id: RequestOrgDep,
    member_scope: MemberScopeDep,
    account_user_id: RequestAccountUserDep,
) -> None:
    """Mark a customer's thread read for the calling staff member."""
    user = await _guard_customer(db, user_id, scope_org_id, member_scope)
    # An X-Admin-Token caller maps to a seeded owner id that may not exist as a
    # real account — nothing to attach a read marker to, so it's a no-op.
    if await db.get(AccountUser, account_user_id) is None:
        return
    now = datetime.now(UTC)
    existing = (
        await db.execute(
            select(ChatRead).where(
                ChatRead.account_user_id == account_user_id, ChatRead.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ChatRead(
                org_id=user.org_id,
                account_user_id=account_user_id,
                user_id=user_id,
                last_read_at=now,
            )
        )
    else:
        existing.last_read_at = now
    await db.commit()
