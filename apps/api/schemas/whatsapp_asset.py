from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Org WhatsApp media library — files the agent can send to a contact via the
# send_whatsapp_file tool (see core/tools.py, db/models/whatsapp_asset.py).
# The uploaded bytes and access_key are never returned — metadata only.


class WhatsAppAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    media_type: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class WhatsAppAssetUpdateIn(BaseModel):
    # Rename / edit the description only. Replacing the file itself means
    # deleting and re-uploading (keeps the route a simple JSON PATCH).
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
