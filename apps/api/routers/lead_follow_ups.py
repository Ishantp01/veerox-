"""Lead follow-up tasks — the dated follow-up slots on a Lead, as one flat list.

Each Lead carries up to three independent follow-ups (``follow_up_at`` /
``follow_up_2_at`` / ``follow_up_3_at`` plus a note and status each). This
router flattens them into one row per scheduled follow-up so the dashboard's
"Follow-up Tasks" page and its due-reminder popup can list them without
opening every lead. It is read-only; edits go through PATCH /admin/leads/{id}
(marking one done just clears its date).

Distinct from routers/follow_ups.py, which is the *automated* WhatsApp
follow-up rules/tasks feature.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from apps.api.db.models import Lead
from apps.api.db.models.account_user import AccountUser
from apps.api.deps import AnalyticsScopeDep, DbDep, MemberScopeDep, verify_admin_or_session

router = APIRouter(
    prefix="/lead-follow-ups",
    tags=["lead-follow-ups"],
    dependencies=[Depends(verify_admin_or_session)],
)

# slot number -> (status attr, at attr, note attr). Slot 1's "status" is the
# lead's own status; slots 2 and 3 have their own.
_SLOTS: dict[int, tuple[str, str, str]] = {
    1: ("status", "follow_up_at", "follow_up_note"),
    2: ("follow_up_2_status", "follow_up_2_at", "follow_up_2_note"),
    3: ("follow_up_3_status", "follow_up_3_at", "follow_up_3_note"),
}


class LeadFollowUpOut(BaseModel):
    lead_id: UUID
    slot: int
    lead_name: str | None
    lead_phone: str | None
    follow_up_at: datetime
    note: str | None
    status: str | None
    channel: str | None
    conversation_id: UUID | None
    claimed_by_account_user_id: UUID | None
    claimed_by_name: str | None


@router.get("", response_model=list[LeadFollowUpOut])
async def list_lead_follow_ups(
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    member_scope: MemberScopeDep,
    due: bool = Query(False, description="Only follow-ups whose time has already arrived"),
    search: str | None = Query(None),
) -> list[LeadFollowUpOut]:
    """Every scheduled follow-up in the org, soonest first.

    A ``role=="member"`` caller only sees follow-ups on leads they claimed;
    admins/owners see their whole org; a superuser sees every org (matching
    the Leads page).
    """
    stmt = select(Lead).where(
        (Lead.follow_up_at.is_not(None))
        | (Lead.follow_up_2_at.is_not(None))
        | (Lead.follow_up_3_at.is_not(None)),
    )
    # Same scope as the Leads page: a customer session sees its own org; the
    # platform owner (superuser / admin token) sees every org's leads.
    if scope_org_id is not None:
        stmt = stmt.where(Lead.org_id == scope_org_id)
    if member_scope is not None:
        stmt = stmt.where(Lead.claimed_by_account_user_id == member_scope)
    leads = list((await db.execute(stmt)).scalars().all())

    claimant_ids = {l.claimed_by_account_user_id for l in leads if l.claimed_by_account_user_id}
    names: dict[UUID, str | None] = {}
    if claimant_ids:
        rows = await db.execute(
            select(AccountUser.id, AccountUser.full_name, AccountUser.email).where(
                AccountUser.id.in_(claimant_ids)
            )
        )
        names = {r.id: r.full_name or r.email for r in rows}

    now = datetime.now(UTC)
    term = (search or "").strip().lower()
    out: list[LeadFollowUpOut] = []
    for lead in leads:
        for slot, (status_attr, at_attr, note_attr) in _SLOTS.items():
            at = getattr(lead, at_attr)
            if at is None:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if due and at > now:
                continue
            note = getattr(lead, note_attr)
            if term and not any(
                term in (f or "").lower() for f in (lead.name, lead.phone, note)
            ):
                continue
            out.append(
                LeadFollowUpOut(
                    lead_id=lead.id,
                    slot=slot,
                    lead_name=lead.name,
                    lead_phone=lead.phone,
                    follow_up_at=at,
                    note=note,
                    status=getattr(lead, status_attr),
                    channel=lead.channel,
                    conversation_id=lead.conversation_id,
                    claimed_by_account_user_id=lead.claimed_by_account_user_id,
                    claimed_by_name=names.get(lead.claimed_by_account_user_id)
                    if lead.claimed_by_account_user_id
                    else None,
                )
            )
    out.sort(key=lambda r: r.follow_up_at)
    return out
