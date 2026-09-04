from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.base import Base
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.models.script import Script


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
    # Set the first (and only) time this org is granted a free (price_cents
    # == 0) plan — see routers/billing.py `create_checkout_session`. Once
    # set, no free plan can be selected or renewed again, even after the
    # org later upgrades to a paid plan or its free credits run out. NULL =
    # never claimed a free plan yet (includes orgs that predate this field).
    free_plan_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Org-authored replacement for the built-in OUTBOUND_CALL_PROMPT (see
    # core/prompts.py) — NULL means "use the platform default script".
    # WhatsApp-only these days (core/agent.py::_system_prompt_for) — voice
    # calling now picks from the `scripts` relationship below instead (see
    # channels/voice/realtime_bridge.py::_system_instructions), one org-wide
    # default plus per-campaign overrides (db/models/call_campaign.py's
    # script_id). Kept here, unmigrated, purely for WhatsApp.
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta's `metadata.phone_number_id` for this org's WhatsApp Business
    # number — every inbound webhook payload carries it, so it's how
    # channels/whatsapp/adapter.py tells which org a message belongs to
    # instead of hardcoding settings.default_org_id. NULL = not yet
    # provisioned with a dedicated number.
    whatsapp_phone_number_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    # This org's dedicated Plivo/Twilio numbers — an org can have several of
    # each (see db/models/org_phone_number.py). Plivo/Twilio's answer webhook
    # passes the dialed number as `To`, letting channels/voice/webhook.py
    # resolve the org for an *inbound* call on any of them; outbound calls
    # round-robin across every row per provider, in `position` order (see
    # channels/voice/org_numbers.py::get_rotating_numbers) — ordered the same
    # way here so the settings page lists numbers in the order they dial.
    phone_numbers: Mapped[list["OrgPhoneNumber"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="OrgPhoneNumber.position"
    )
    # This org's AI-calling script library (see db/models/script.py) — voice
    # only. Exactly one row is expected to carry is_default=True; that's the
    # base a campaign call falls back to when it has no script_id of its own.
    scripts: Mapped[list["Script"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    # Explicit override of failover.py's automatic Plivo-first/Twilio-
    # fallback ordering — "plivo", "twilio", or NULL (automatic, the
    # default: prefer whichever provider this org has a dedicated number
    # on, Plivo if both/neither). Applied at every outbound-calling entry
    # point (single admin call, AI callback, campaign dialer, follow-up
    # dispatcher) via `initiate_call`'s `preferred_provider` kwarg — see
    # routers/admin.py's PUT /admin/settings/calling.
    preferred_voice_provider: Mapped[str | None] = mapped_column(String(10), nullable=True)
