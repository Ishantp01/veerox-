from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TemplateCreate(BaseModel):
    name: str
    language: str = "en_US"
    category: str | None = None
    param_labels: list[str] = []
    body_preview: str | None = None
    active: bool = True


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    language: str
    category: str | None
    param_labels: list[str]
    body_preview: str | None
    active: bool
    created_at: datetime
    # Live Meta review status (PENDING/APPROVED/REJECTED), matched by name+
    # language against the WABA's message_templates. None when Meta's API
    # couldn't be reached or no matching template was found there.
    meta_status: str | None = None


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    language: str | None = None
    category: str | None = None
    param_labels: list[str] | None = None
    body_preview: str | None = None
    active: bool | None = None
