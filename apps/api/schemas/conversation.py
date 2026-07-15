from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    user_id: UUID
    channel: str
    started_at: datetime
    ended_at: datetime | None
    recording_url: str | None = None
    recording_duration_secs: float | None = None


class ConversationSummaryOut(ConversationOut):
    """ConversationOut plus a message count — used wherever a conversation is
    listed without its full transcript (GET /admin/conversations, and the
    lead-detail conversation-history list)."""

    message_count: int


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    conversation_id: UUID
    user_id: UUID
    role: str
    content: str
    channel: str
    tokens_in: int | None
    tokens_out: int | None
    audio_secs: float | None
    created_at: datetime
