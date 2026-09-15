from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Script(Base):
    """One named AI script an org can pick per campaign (see
    db/models/call_campaign.py's script_id / whatsapp_script_id) or leave as
    the org's default for its channel, used whenever a campaign doesn't pick
    one — see channels/voice/realtime_bridge.py::_system_instructions
    (voice) and core/agent.py::_system_prompt_for (whatsapp) for the
    resolution order.

    `channel` splits the library in two ("voice" | "whatsapp") — a voice
    script is never offered for a WhatsApp campaign or vice versa. Existing
    rows predate this column and backfill to "voice" (WhatsApp previously had
    no script library at all, just the single Org.script text column, kept
    as a further fallback for orgs with no WhatsApp Script rows yet — see
    db/models/org.py).

    Exactly one row per (org_id, channel) is expected to carry
    is_default=True. Enforced in application code, not a DB constraint —
    same reasoning as OrgPhoneNumber.is_default (a partial unique index
    doesn't translate to the SQLite backend the test suite runs against).
    """

    __tablename__ = "scripts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default="voice", server_default="voice")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Which of the org's WhatsApp numbers this script belongs to, set from
    # the settings page. Doesn't affect which number a send goes OUT from
    # (that's still per-campaign CallCampaign.whatsapp_number_id or the
    # org's default WhatsApp number) — but it IS used to pick the reply
    # script for an inbound message on this number when no campaign has
    # already pinned a more specific one (see
    # core/agent.py::_resolve_whatsapp_script_content). Only meaningful for
    # channel="whatsapp"; nulled out if the referenced number is deleted.
    phone_number_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("org_phone_numbers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
