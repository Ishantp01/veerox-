from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class UsageCounter(Base):
    """Monthly per-org usage counter (call_minutes, whatsapp_messages,
    leads_created, ...), incremented at the point usage happens and read by
    plan-limit checks (apps/api/deps.py) and usage-based billing.
    """

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "metric", "period_start", name="uq_usage_counters_org_metric_period"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default="0")
