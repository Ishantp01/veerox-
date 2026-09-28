from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# Documented AWS spend categories (req §9) — an `aws_service` (e.g. "AmazonEC2",
# "AmazonRDS") maps to exactly one of these via
# workers/aws_cost_importer.py::_categorize_service.
AWS_COST_POOL_CATEGORIES = (
    "compute",
    "database",
    "storage",
    "network",
    "serverless",
    "queue",
    "logging",
    "misc",
)


class AwsCostPool(Base):
    """Actual AWS spend for one billing period/account/service, imported by
    workers/aws_cost_importer.py from Cost Explorer (or manually entered —
    see `source`). This is the ground truth req §10's allocation formula
    divides up across organizations; it is never itself org-scoped.
    """

    __tablename__ = "aws_cost_pools"
    __table_args__ = (
        UniqueConstraint(
            "billing_period", "aws_account_id", "aws_service",
            name="uq_aws_cost_pools_period_account_service",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    aws_account_id: Mapped[str] = mapped_column(String(20), nullable=False)
    aws_service: Mapped[str] = mapped_column(String(100), nullable=False)
    pool_category: Mapped[str] = mapped_column(String(20), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    # "cost_explorer" | "cur" | "manual" — how this row was populated.
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
