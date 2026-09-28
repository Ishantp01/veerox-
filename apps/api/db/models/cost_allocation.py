from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class CostAllocation(Base):
    """One org's (or the platform's Shared/Unallocated bucket's) estimated
    share of one AWS cost-pool category for one billing period, written by
    workers/cost_allocation_worker.py per req §10's formula:

        AllocatedCost = PoolTotalCost * OrgMetric / TotalMetricForAllOrgs

    `org_id IS NULL` is the reserved row representing the remainder that
    couldn't be attributed to any specific organization (rounding, orgs with
    zero measured metric, genuinely shared overhead) — always present
    alongside the per-org rows for a given (billing_period, pool_category,
    allocation_version) so `sum(allocated_cost)` across all rows in that
    group reconciles exactly to the pool's `total_cost`.

    Never overwritten in place: a new allocation run for an already-
    calculated period bumps `allocation_version` and inserts a fresh set of
    rows rather than mutating the old ones, so a past bill's numbers stay
    reconstructable even if the formula/metric changes later (req §10).
    """

    __tablename__ = "cost_allocations"
    __table_args__ = (
        UniqueConstraint(
            "billing_period", "org_id", "pool_category", "allocation_version",
            name="uq_cost_allocations_period_org_category_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    org_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
    )
    pool_category: Mapped[str] = mapped_column(String(20), nullable=False)
    allocated_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    # e.g. "worker_job_seconds", "database_operations", "bandwidth_gb" — the
    # documented metric this category was allocated by (req §10).
    metric_used: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, server_default="0"
    )
    total_metric_value: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), nullable=False, server_default="0"
    )
    # Always true for a real org row (this is an estimate off a shared-infra
    # proxy metric, never an exact per-tenant AWS invoice); false only for
    # rows where a metric happens to be an exact accounting (kept for
    # forward-compatibility — every row today is estimated).
    is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    allocation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
