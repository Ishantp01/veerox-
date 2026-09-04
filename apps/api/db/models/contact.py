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

    Visibility is siloed by creator: routers/crm.py's list/get/update/delete
    only ever see a contact where `created_by_account_user_id` matches the
    caller, for EVERY role including admin — unlike Lead (see
    Lead.claimed_by_account_user_id), there's no org-wide visibility
    exception here. Nullable because contacts written before this column
    existed (or by very old import paths) have no recorded creator, and are
    therefore nobody's — visible to no one via the API without a direct DB
    fix, same as any orphaned row.

    Phone uniqueness is scoped per (org_id, phone, created_by_account_user_id)
    — not just (org_id, phone) — since two team members' contact lists are
    independent: rep A and rep B can each have their own contact for the
    same phone number (e.g. both talked to the same person), and importing/
    adding a number someone else in the org already has should add it to
    *your* list, not be blocked by their row. NULLs (orphaned, creator-less
    rows) each count as distinct under Postgres's NULL-handling in unique
    indexes, so they never collide with each other either.
    """

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "phone", "created_by_account_user_id", name="uq_contacts_org_phone_creator"
        ),
    )

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
    created_by_account_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_users.id", ondelete="SET NULL"), nullable=True
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
