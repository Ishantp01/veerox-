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
    created_at: datetime
    updated_at: datetime


class ContactWithLeadsOut(ContactOut):
    leads: list[LeadOut]


class ContactUpdateIn(BaseModel):
    """Partial update — only fields explicitly set by the caller are applied."""

    name: str | None = None
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
