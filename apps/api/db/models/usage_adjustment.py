from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# "late_event": a UsageEvent arrived after its billing period was locked.
# "rounding": reconciling actual vs. allocated AWS cost left a small
# remainder (req §16 step 6). "shared_overhead": a manual allocation of the
# Shared/Unallocated bucket. "manual": a platform admin's own correction.
USAGE_ADJUSTMENT_REASONS = ("late_event", "rounding", "shared_overhead", "manual")


class UsageAdjustment(Base):
    """A correction to an org's billing for a period, without ever mutating
    a locked `UsageMonthly` or `Invoice` row (req §16: "closed billing
    periods must not be silently overwritten... late events must create
    adjustment records"). The next open invoice/statement folds these in.
    """

    __tablename__ = "usage_adjustments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    # The UsageEvent.event_id that triggered this adjustment, if any — not a
    # DB foreign key (the event and the adjustment can outlive each other
    # independently; this is an audit pointer, not a referential constraint).
    related_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_account_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
