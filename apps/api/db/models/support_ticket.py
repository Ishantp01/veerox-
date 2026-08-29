from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# Kept as plain string columns (not Postgres native enums) to match the rest
# of the codebase's status-column convention (e.g. Lead.status, FollowUpTask.status).
TICKET_CATEGORIES = ("bug", "billing", "feature_request", "urgent", "other")
TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    account_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account_users.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, server_default="other")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open")
    # Best-effort context auto-attached from the browser at submit time
    # (document.referrer) — the page the error actually happened on, since
    # the ticket form itself is a separate page. Nullable: not every client
    # sends a referrer (direct nav, privacy settings).
    page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
