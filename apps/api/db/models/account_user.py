from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class AccountUser(Base):
    """A dashboard login account. Distinct from `User`
    (apps/api/db/models/user.py), which is a WhatsApp/voice contact identity
    keyed off phone number — this is the entity that logs into the admin
    dashboard with email + password and holds `OrgMembership` rows.
    """

    __tablename__ = "account_users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # SHA-256 digest of the permanent login token (see core/security.py) —
    # not a password hash. Unique + indexed so login can look a user up
    # directly by the token they present.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # `default=` (Python-side, applied before INSERT) alongside `server_default`
    # (Postgres DDL) — SQLite's DDL default text isn't reliably coerced back to
    # a real bool on read (the literal 'false' round-trips as a truthy
    # non-empty string), which silently made every test-DB account a
    # superuser. Postgres in production parses server_default correctly either
    # way; `default=` makes ORM-created rows correct on both.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
