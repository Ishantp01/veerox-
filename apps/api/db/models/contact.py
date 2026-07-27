from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.base import Base


class Contact(Base):
    """Unified CRM entity a rep works with across channels. Distinct from
    `User` (the per-channel messaging identity the webhook pipeline keys off)
    — a Contact is the cross-channel parent that `Lead` rows optionally roll
    up under via `Lead.contact_id`.
    """

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("org_id", "phone", name="uq_contacts_org_phone"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    leads: Mapped[list["Lead"]] = relationship(back_populates="contact")  # noqa: F821
