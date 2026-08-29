"""Support tickets: any logged-in dashboard user can raise one when they hit
an error, and it's routed straight to the Veerox platform team — not the
org's own admin. Two routers live here:

- `router` (/tickets) — org self-service: raise a ticket, see your own org's
  tickets. Scoped by `CurrentOrgDep`/`CurrentUserDep` exactly like team.py.
- `admin_router` (/admin/tickets) — the platform team's queue: every org's
  tickets in one place, filterable by status, with a status-update endpoint.
  Guarded by `verify_platform_team_member` (deps.py) — the shared
  `X-Admin-Token`, or any session whose org membership is the platform
  operator's own org — so only Veerox staff reach it, and once they do they
  see every org's tickets, not just their own (see list_all_tickets).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from apps.api.db.models.account_user import AccountUser
from apps.api.db.models.org import Org
from apps.api.db.models.support_ticket import TICKET_CATEGORIES, TICKET_STATUSES, SupportTicket
from apps.api.deps import (
    CurrentOrgDep,
    CurrentUserDep,
    DbDep,
    verify_platform_team_member,
)
from apps.api.schemas.support_ticket import (
    TICKET_STATUS_PATTERN,
    AdminTicketOut,
    TicketCreateIn,
    TicketOut,
    TicketStatusUpdateIn,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])
admin_router = APIRouter(
    prefix="/admin/tickets",
    tags=["admin-tickets"],
    dependencies=[Depends(verify_platform_team_member)],
)


@router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(
    payload: TicketCreateIn, org: CurrentOrgDep, current_user: CurrentUserDep, db: DbDep
) -> TicketOut:
    if not payload.subject.strip():
        raise HTTPException(status_code=400, detail="Subject is required")
    if not payload.description.strip():
        raise HTTPException(status_code=400, detail="Description is required")
    if payload.category not in TICKET_CATEGORIES:
        raise HTTPException(
            status_code=400, detail=f"category must be one of {TICKET_CATEGORIES}"
        )

    ticket = SupportTicket(
        org_id=org.org_id,
        account_user_id=current_user.id,
        subject=payload.subject.strip(),
        description=payload.description.strip(),
        category=payload.category,
        page_url=payload.page_url,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return TicketOut.model_validate(ticket)


@router.get("", response_model=list[TicketOut])
async def list_my_org_tickets(org: CurrentOrgDep, db: DbDep) -> list[TicketOut]:
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.org_id == org.org_id)
        .order_by(SupportTicket.created_at.desc())
    )
    return [TicketOut.model_validate(t) for t in result.scalars().all()]


def _admin_ticket_out(ticket: SupportTicket, org: Org, account_user: AccountUser) -> AdminTicketOut:
    return AdminTicketOut(
        **TicketOut.model_validate(ticket).model_dump(),
        org_name=org.name,
        account_user_email=account_user.email,
        account_user_name=account_user.full_name,
    )


@admin_router.get("", response_model=list[AdminTicketOut])
async def list_all_tickets(
    db: DbDep,
    status: str | None = Query(None, pattern=TICKET_STATUS_PATTERN),
) -> list[AdminTicketOut]:
    """Platform-wide ticket queue — every org's tickets. No per-caller org
    scoping here: `verify_platform_team_member` has already restricted
    reaching this route to Veerox staff, so unlike admin.py's endpoints
    (reachable by any customer session and scoped down to their own org)
    there's no "customer org" case to narrow for."""
    stmt = (
        select(SupportTicket, Org, AccountUser)
        .join(Org, Org.id == SupportTicket.org_id)
        .join(AccountUser, AccountUser.id == SupportTicket.account_user_id)
        .order_by(SupportTicket.created_at.desc())
    )
    if status:
        stmt = stmt.where(SupportTicket.status == status)

    rows = (await db.execute(stmt)).all()
    return [_admin_ticket_out(ticket, org, account_user) for ticket, org, account_user in rows]


@admin_router.patch("/{ticket_id}", response_model=AdminTicketOut)
async def update_ticket_status(
    ticket_id: UUID, payload: TicketStatusUpdateIn, db: DbDep
) -> AdminTicketOut:
    if payload.status not in TICKET_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {TICKET_STATUSES}")

    result = await db.execute(
        select(SupportTicket, Org, AccountUser)
        .join(Org, Org.id == SupportTicket.org_id)
        .join(AccountUser, AccountUser.id == SupportTicket.account_user_id)
        .where(SupportTicket.id == ticket_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket, org, account_user = row

    ticket.status = payload.status
    if payload.status == "resolved" and ticket.resolved_at is None:
        ticket.resolved_at = datetime.now(UTC)
    elif payload.status != "resolved":
        ticket.resolved_at = None

    await db.commit()
    await db.refresh(ticket)
    return _admin_ticket_out(ticket, org, account_user)
