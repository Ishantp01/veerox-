from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class UsageDaily(Base):
    """One org/day/service/usage_type rollup of `UsageEvent`, written by
    workers/usage_daily_aggregator.py.

    The unique constraint below is what makes the aggregator idempotent —
    it upserts on this key, so re-running the same day twice (including a
    partially-elapsed "today") never double-counts.
    """

    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "usage_date", "service", "usage_type",
            name="uq_usage_daily_org_date_service_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)
    service: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    total_quantity: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, server_default="0"
    )
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Third-party cost estimate for this rollup, from CostRate — NULL when no
    # rate is configured for this service/usage_type/provider combination
    # (e.g. pure AWS-infrastructure rows have no third-party cost).
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
