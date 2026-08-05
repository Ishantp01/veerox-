from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# Roles a membership can hold, from most to least privileged. Kept as a plain
# string column (not a Postgres native enum) to match the rest of the
# codebase's status-column convention (e.g. Lead.status, FollowUpTask.status).
ORG_MEMBERSHIP_ROLES = ("admin", "member")


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    __table_args__ = (
        UniqueConstraint("org_id", "account_user_id", name="uq_org_memberships_org_account_user"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    account_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("account_users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_users.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
