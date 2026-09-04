from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.lead import LeadOut


class ContactCreate(BaseModel):
    name: str | None = None
    phone: str
    email: str | None = None
    company: str | None = None
    tags: list[str] | None = None
    owner_user_id: UUID | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str | None
    phone: str
    email: str | None
    company: str | None
    tags: list[str] | None
    owner_user_id: UUID | None
    # Who created this contact — visibility is siloed on this field (see
    # db/models/contact.py); read-only, not settable via ContactCreate/
    # ContactUpdateIn.
    created_by_account_user_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ContactWithLeadsOut(ContactOut):
    leads: list[LeadOut]


class ContactUpdateIn(BaseModel):
    """Partial update — only fields explicitly set by the caller are applied.

    `phone` is optional but, when set, must stay unique within the org (see
    Contact's uq_contacts_org_phone) — routers/crm.py::update_contact 409s
    on a conflict, same as POST /crm/contacts."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    company: str | None = None
    tags: list[str] | None = None
    owner_user_id: UUID | None = None


class ContactImportError(BaseModel):
    row: int
    reason: str


class ContactImportResult(BaseModel):
    imported: int
    updated: int
    skipped: int
    errors: list[ContactImportError]
