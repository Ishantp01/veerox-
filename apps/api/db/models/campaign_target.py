from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("call_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    # pending -> calling -> completed | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    # Null until the AI reaches a verdict (call may still fail before that).
    qualified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    disposition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
