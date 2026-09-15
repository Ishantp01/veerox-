from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TemplateButton(BaseModel):
    type: str  # QUICK_REPLY | URL | PHONE_NUMBER | COPY_CODE
    text: str | None = None
    url: str | None = None
    phone_number: str | None = None
    # Example value: URL's {{1}} sample (if the url has a variable), or the
    # sample coupon/offer code for COPY_CODE. Unused for QUICK_REPLY/PHONE_NUMBER.
    example: str | None = None


class TemplateCreate(BaseModel):
    name: str
    language: str = "en_US"
    category: str | None = None
    param_labels: list[str] = []
    body_preview: str | None = None
    header_type: str | None = None  # TEXT | IMAGE | VIDEO | DOCUMENT
    header_text: str | None = None
    header_example: str | None = None
    # For an IMAGE/VIDEO/DOCUMENT header: an existing WhatsAppAsset to upload
    # to Meta for the header's media handle. Ignored for a TEXT header.
    header_asset_id: UUID | None = None
    footer_text: str | None = None
    buttons: list[TemplateButton] = []
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
    header_type: str | None = None
    header_text: str | None = None
    header_example: str | None = None
    footer_text: str | None = None
    buttons: list[TemplateButton] = []
    active: bool
    created_at: datetime
    # Live Meta review status (PENDING/APPROVED/REJECTED), matched by name+
    # language against the WABA's message_templates. None when Meta's API
    # couldn't be reached or no matching template was found there.
    meta_status: str | None = None


class TemplateSyncResult(BaseModel):
    created: list[TemplateOut]
    # Existing rows that had header_type/footer_text/buttons backfilled from
    # Meta's live definition (only ever fills gaps, never overwrites).
    updated: list[TemplateOut] = []
    skipped: int
    total_on_meta: int


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    language: str | None = None
    category: str | None = None
    param_labels: list[str] | None = None
    body_preview: str | None = None
    header_type: str | None = None
    header_text: str | None = None
    header_example: str | None = None
    footer_text: str | None = None
    buttons: list[TemplateButton] | None = None
    active: bool | None = None
