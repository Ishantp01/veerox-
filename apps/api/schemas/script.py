from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Voice-only script library — one org can have several named scripts,
# picked per campaign (see schemas/campaign.py's script_id) or left to fall
# back to whichever one is_default. Separate from schemas/admin.py's
# ScriptIn/ScriptOut, the singleton override still used by WhatsApp.


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    content: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ScriptCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    is_default: bool = Field(
        False,
        description="Ignored (forced true) for an org's very first script — an org is never "
        "left with zero default scripts once it has at least one.",
    )


class ScriptUpdateIn(BaseModel):
    # Rename/edit content only — setting the default is its own endpoint
    # (POST /admin/scripts/{id}/set-default) since it also has to unset the
    # org's previous default in the same transaction.
    name: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
