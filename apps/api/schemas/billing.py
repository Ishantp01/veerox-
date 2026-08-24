from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PlanOut(BaseModel):
    code: str
    name: str
    price_cents: int
    limits: dict[str, Any]
    # None = a full subscription plan. One of Plan.PLAN_RESOURCE_TYPES = a
    # single-resource recharge/top-up SKU (see apps/api/db/models/plan.py).
    resource_type: str | None = None


class PlanAdminOut(BaseModel):
    code: str
    name: str
    price_cents: int
    limits: dict[str, Any]
    is_active: bool
    resource_type: str | None = None


class RegenerateAdminTokenOut(BaseModel):
    account_user_id: str
    email: str
    # Shown exactly once — only the SHA-256 digest is stored server-side.
    # The previous token stops working immediately.
    login_token: str


class OrgAdminOut(BaseModel):
    id: str
    name: str
    plan_code: str | None
    billing_status: str
    seat_count: int
    admin_email: str | None
    created_at: str


class PlanCreateIn(BaseModel):
    code: str
    name: str
    price_cents: int
    limits: dict[str, Any]
    is_active: bool = True
    resource_type: str | None = None


class PlanUpdateIn(BaseModel):
    """All fields optional — only what's sent gets changed (PATCH semantics)."""

    name: str | None = None
    price_cents: int | None = None
    limits: dict[str, Any] | None = None
    is_active: bool | None = None
    resource_type: str | None = None


class BillingStatusOut(BaseModel):
    billing_status: str
    plan: PlanOut | None
    seat_count: int
    # When the org's current credits were bought. Informational only —
    # nothing expires on a timer any more, access ends when the credits in
    # BillingUsageOut run out (see core/usage.py).
    last_recharge_at: str | None


class UsageMetricOut(BaseModel):
    used: float
    limit: float | None


class BillingUsageOut(BaseModel):
    # Start of the *credit* period — the org's last recharge, not a
    # calendar month boundary.
    period_start: str
    campaigns: UsageMetricOut
    call_minutes: UsageMetricOut
    whatsapp_messages: UsageMetricOut


class CheckoutSessionIn(BaseModel):
    plan_code: str
    success_url: str
    cancel_url: str


class CheckoutSessionOut(BaseModel):
    # Free plans: set, frontend redirects straight there (no Razorpay
    # involved). Paid plans: null — the frontend instead uses order_id +
    # razorpay_key_id to launch Razorpay's embedded Checkout modal for a
    # one-time payment, then confirms via POST /billing/verify-payment.
    checkout_url: str | None = None
    order_id: str | None = None
    amount_cents: int | None = None
    razorpay_key_id: str | None = None


class VerifyPaymentIn(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


class PlatformSettingsOut(BaseModel):
    help_desk_script: str | None
    social_links: dict[str, str]


class PlatformSettingsUpdateIn(BaseModel):
    """All fields optional — only what's sent gets changed (PATCH semantics)."""

    help_desk_script: str | None = None
    social_links: dict[str, str] | None = None


class SocialLinksOut(BaseModel):
    social_links: dict[str, str]
