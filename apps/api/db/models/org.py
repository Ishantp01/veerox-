from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    # trialing -> active -> past_due -> canceled | incomplete
    billing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="trialing"
    )
    # When the org's *current* plan_id took effect, i.e. its last recharge.
    # Usage aggregation (core/usage.py) counts from here, so moving this
    # forward is what restores an org's credits — there is no calendar
    # reset, and a plan change isn't eaten by usage accrued under the
    # previous plan.
    plan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Org-specific effective resource limits, overriding Plan.limits once
    # populated — this is what lets a single-resource recharge (see
    # routers/billing.py `_activate_paid_payment`) top up e.g. call minutes
    # without disturbing WhatsApp messages/team members/campaigns. NULL
    # means "never touched by a recharge under this scheme yet" — every
    # metric falls back to the current plan's `limits` wholesale, so
    # existing orgs are unaffected until their next recharge (see
    # deps.py `effective_limits`).
    resource_limits: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Org-authored replacement for the built-in OUTBOUND_CALL_PROMPT (see
    # core/prompts.py) — NULL means "use the platform default script".
    # Shared by both WhatsApp and voice, same as the prompt it replaces.
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta's `metadata.phone_number_id` for this org's WhatsApp Business
    # number — every inbound webhook payload carries it, so it's how
    # channels/whatsapp/adapter.py tells which org a message belongs to
    # instead of hardcoding settings.default_org_id. NULL = not yet
    # provisioned with a dedicated number.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    # This org's dedicated Plivo number (E.164) — Plivo's answer webhook
    # passes the dialed number as `To`, letting channels/voice/webhook.py
    # resolve the org for an *inbound* call the same way. NULL = not yet
    # provisioned with a dedicated number.
    plivo_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
    # This org's dedicated Twilio number (E.164), mutually exclusive with
    # plivo_phone_number — set instead of it when the number an admin enters
    # is found in the Twilio account rather than the Plivo one (see
    # channels/voice/number_provider.py::detect_provider), so a call can
    # fail over from Plivo to Twilio (or vice versa) while still dialing
    # from this org's own number on whichever provider actually owns it.
    twilio_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True)
