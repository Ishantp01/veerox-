from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class CallCampaign(Base):
    __tablename__ = "call_campaigns"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Free-text qualification bar the agent evaluates each prospect against
    # (e.g. "must confirm interest in a demo and have budget above $5,000").
    criteria: Mapped[str] = mapped_column(Text, nullable=False)
    # Display-only summary of which channel(s) this campaign's targets use —
    # "voice", "whatsapp", or "mixed" when it has both. NOT read by either
    # worker: routing is decided per-target by CampaignTarget.channel, since
    # one upload's rows can resolve to different channels each. Computed once
    # at creation time in admin.py's _create_campaign_from_rows from the
    # distinct channels actually inserted.
    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="voice")
    # draft (created, inert) -> scheduled (has a future scheduled_start_at) ->
    # running (the only status either worker acts on) -> paused | completed.
    # Defaults to "draft" so uploading a list never starts outreach on its
    # own — an explicit start/schedule action is required.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="draft")
    scheduled_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # WhatsApp-only: when set, the dispatcher sends this pre-approved Meta
    # template as the first-touch message instead of free text, sidestepping
    # the 24h customer-service-window restriction on cold outbound contacts.
    template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Ordered per-placeholder source for the template's {{1}}, {{2}}, ...
    # body variables. Each entry is either a dynamic token resolved fresh at
    # send time — "{{contact_name}}" (CampaignTarget.name), "{{send_date}}",
    # "{{send_time}}" (both in IST, resolved the moment the message actually
    # sends, not when the campaign was created) — or literal custom text
    # used as-is. See workers/whatsapp_dispatcher.py's
    # _resolve_template_body_params.
    template_params: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Optional free-text follow-up sent right after the template (or after
    # the default opening message, if no template is set).
    custom_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Voice-only overrides, both optional — NULL falls back to the org's
    # default behavior (see channels/voice/realtime_bridge.py::
    # _system_instructions for script, workers/campaign_dialer.py::
    # _claim_targets for phone_number). SET NULL on delete so removing a
    # script/number an old campaign referenced never blocks the delete or
    # loses the campaign — it just falls back.
    script_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True
    )
    phone_number_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("org_phone_numbers.id", ondelete="SET NULL"), nullable=True
    )
    # Voice-only: how many times the dialer (workers/campaign_dialer.py) will
    # place a call to a target that never connects before giving up and
    # marking it "failed". Selectable per campaign at creation (any integer
    # >= 1); defaults to 3, the old hard-coded value. A target that actually
    # answered is never retried regardless of this (see handle_call_ended).
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
