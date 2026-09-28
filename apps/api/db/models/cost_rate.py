from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class CostRate(Base):
    """A documented per-unit rate used to turn third-party usage into an
    estimated cost (workers/usage_daily_aggregator.py). Admin-editable via
    GET/POST /admin/cost-rates (routers/usage.py) rather than hardcoded, so
    a rate change is itself auditable (created_at + effective_from). With
    no rate configured for a given (service, usage_type[, provider]), that
    usage is still metered (a UsageEvent/UsageDaily row exists) but never
    priced — estimated_cost stays NULL everywhere it's shown.

    Not org-scoped — rates are platform-wide per provider/service/usage_type;
    an org's own negotiated rate, if ever needed, would be a separate
    override table rather than complicating this one.
    """

    __tablename__ = "cost_rates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    service: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
