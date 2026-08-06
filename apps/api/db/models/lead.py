from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.base import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    # Set only by transfer_to_human — the conversation the escalation was
    # raised from, so operators can jump straight to the transcript.
    # Other Lead-creation paths (capture_lead, campaign qualification) leave
    # this null.
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Free-form multi-tag classification, layered on top of `intent` (the
    # AI-captured single reason-for-contact) — lets a rep classify a lead
    # along several axes at once (e.g. "hot", "enterprise", "needs-demo").
    # Mirrors Contact.tags (db/models/contact.py).
    tags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="new")
    follow_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_up_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Explicit qualification workflow, distinct from `status` — a lead can sit
    # in status="contacted" while a rep separately works it through
    # unqualified -> in_review -> qualified/disqualified.
    qualification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="unqualified"
    )
    qualification_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qualification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # $-denominated pipeline value — the cheapest path to a revenue view
    # without a full Deal entity (see routers/sales.py). Nullable: most leads
    # never get one set, and that's fine, they just don't count toward
    # pipeline/revenue totals.
    deal_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    contact: Mapped["Contact | None"] = relationship(back_populates="leads")  # noqa: F821
