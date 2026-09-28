from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# One row per licence lifecycle action taken via routers/billing.py's
# license endpoints or routers/platform.py's organization creation — the
# audit history required by the licensing spec, independent of Org's own
# current-state columns (which only ever hold the latest values).
LICENSE_AUDIT_ACTIONS = (
    "issued",
    "renewed",
    "extended",
    "suspended",
    "revoked",
    "reactivated",
)


class LicenseAuditEvent(Base):
    __tablename__ = "license_audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # NULL for actions taken by the shared X-Admin-Token (no specific
    # account attached) — same convention as other admin-attributed actions
    # in this codebase (e.g. OrgMembership.invited_by_id).
    actor_account_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_users.id", ondelete="SET NULL"), nullable=True
    )
    previous_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    previous_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
