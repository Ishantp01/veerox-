from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    # trialing -> active -> past_due -> canceled | incomplete
    billing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="trialing"
    )
    # When the org's *current* plan_id took effect — usage aggregation
    # (core/usage.py) counts from here rather than the calendar month's
    # start, so a plan upgrade isn't immediately eaten by usage already
    # accrued under the previous plan.
    plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
