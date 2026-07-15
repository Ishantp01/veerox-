from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="new")
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_up_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
