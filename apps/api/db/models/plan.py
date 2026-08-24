from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

PLAN_CODES = ("basic", "pro", "premium")

# A Plan is either a full subscription bundle (resource_type is None, covers
# all four resources at once) or a single-resource recharge/top-up SKU whose
# `limits` JSON carries exactly one of these keys with the amount to add.
PLAN_RESOURCE_TYPES = (
    "max_call_minutes",
    "max_whatsapp_messages",
    "max_team_members",
    "max_campaigns",
)

# Maps a recharge SKU's `resource_type` label to the real key in
# Plan.limits/Org.resource_limits it increments. Only "max_team_members"
# differs from its own name — it exists purely so a recharge-only Plan can
# read more clearly than the underlying "max_seats" limit key, which is used
# unchanged by every pre-existing call site (routers/team.py, deps.py, the
# limits JSON of *full* plans, etc.). Every other resource maps to itself.
RESOURCE_TYPE_LIMIT_KEY = {
    "max_call_minutes": "max_call_minutes",
    "max_whatsapp_messages": "max_whatsapp_messages",
    "max_team_members": "max_seats",
    "max_campaigns": "max_campaigns",
}


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # One-time price per recharge, not a monthly subscription fee — plans
    # are bought outright and last until their credits run out (see
    # core/usage.py).
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # e.g. {"max_seats": 5, "max_campaigns": 20}
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # None = a full subscription plan (unchanged legacy behavior: buying it
    # replaces the org's plan_id and resets every resource). One of
    # PLAN_RESOURCE_TYPES = a single-resource recharge SKU — buying it only
    # tops up that one resource on the org (see routers/billing.py
    # `_activate_paid_payment`), leaving the other three untouched.
    resource_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
