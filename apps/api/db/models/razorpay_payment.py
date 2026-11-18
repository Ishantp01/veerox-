"""Razorpay one-time plan purchases — deliberately independent of the
manual org-license system (Org.license_*): nothing here reads or writes it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class PaymentPlan(Base):
    __tablename__ = "payment_plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Smallest currency unit (paise for INR) — what Razorpay expects.
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Add-on pack: bought on top of a running plan. Its max_* values are EXTRA
    # amounts added to the base plan's limits (NULL = adds nothing) and it
    # expires with the base plan's current period.
    is_addon: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    # Usage limits while this plan is active. NULL = unlimited. See core/plan_limits.py.
    max_call_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_whatsapp_messages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_team_members: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_voice_campaigns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_whatsapp_campaigns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RazorpayPayment(Base):
    """One row per Razorpay Order. `status`: created -> paid | failed."""

    __tablename__ = "razorpay_payments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("payment_plans.id"), nullable=False)
    razorpay_order_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="created")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
