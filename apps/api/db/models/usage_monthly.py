from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# "pending": not yet calculated for this period. "calculated": aggregation +
# allocation have run but the period is still open (can still change).
# "locked": workers/billing_worker.py has closed this period — never
# overwritten again; a late event creates a UsageAdjustment instead.
USAGE_MONTHLY_STATUSES = ("pending", "calculated", "locked")


class UsageMonthly(Base):
    """One org/billing_period/service/usage_type rollup of `UsageDaily`,
    written by workers/usage_monthly_aggregator.py.
    """

    __tablename__ = "usage_monthly"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "billing_period", "service", "usage_type",
            name="uq_usage_monthly_org_period_service_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    # "YYYY-MM", e.g. "2026-09" — a string, not a Date, since a billing
    # period has no single canonical day and every query filters on it
    # exactly (never a range).
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    service: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    total_quantity: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, server_default="0"
    )
    # Reserved, currently unused: allocation happens per AWS cost-pool
    # *category* (compute/database/storage/...), which has no natural 1:1
    # mapping onto a service/usage_type row here, so nothing populates this
    # column. An org's allocated AWS cost lives in `CostAllocation` and is
    # read via workers/cost_allocation_worker.py::get_org_allocated_cost —
    # every consumer (routers/usage.py's dashboards, workers/billing_worker.py's
    # invoices) must go through that function, never this column.
    allocated_aws_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    # Third-party (OpenAI/Plivo/Twilio/Meta) estimated cost, summed from
    # UsageDaily.estimated_cost for the period.
    third_party_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    platform_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    total_estimated_charge: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), nullable=True)
    calculation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    # Bumped by workers/cost_allocation_worker.py every time the allocation
    # formula/metric changes — never recomputed retroactively under an old
    # version (req §10: "do not silently change allocation rules between
    # billing periods").
    allocation_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
