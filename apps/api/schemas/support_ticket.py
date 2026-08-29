from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from apps.api.db.models.support_ticket import TICKET_CATEGORIES, TICKET_STATUSES

TICKET_CATEGORY_PATTERN = f"^({'|'.join(TICKET_CATEGORIES)})$"
TICKET_STATUS_PATTERN = f"^({'|'.join(TICKET_STATUSES)})$"


class TicketCreateIn(BaseModel):
    subject: str
    description: str
    category: str = "other"
    # Best-effort: the page the error happened on, captured client-side via
    # document.referrer since the ticket form itself is a separate page.
    page_url: str | None = None


class TicketOut(BaseModel):
    id: UUID
    org_id: UUID
    account_user_id: UUID
    subject: str
    description: str
    category: str
    status: str
    page_url: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class AdminTicketOut(TicketOut):
    """Same as TicketOut, plus who/where it's from — only meaningful once a
    ticket is viewed across orgs (the platform-admin queue), since a
    customer viewing their own tickets already knows both."""

    org_name: str
    account_user_email: str
    account_user_name: str | None = None


class TicketStatusUpdateIn(BaseModel):
    status: str
