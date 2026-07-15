from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Plivo's Call UUID for voice conversations — lets the recording-finished
    # webhook (which only reports CallUUID) find its way back to this row.
    plivo_call_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Plivo hosts the audio file itself; we just store the URL + duration it
    # gives us in the recording callback (see channels/voice/webhook.py).
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    recording_duration_secs: Mapped[float | None] = mapped_column(Float, nullable=True)
