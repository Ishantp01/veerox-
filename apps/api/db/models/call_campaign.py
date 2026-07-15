from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
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
    # Which outreach worker drives this campaign's targets — the voice dialer
    # (apps/api/workers/campaign_dialer.py) or the WhatsApp dispatcher
    # (apps/api/workers/whatsapp_dispatcher.py). "voice" default preserves the
    # behavior of campaigns created before this column existed.
    channel: Mapped[str] = mapped_column(String(10), nullable=False, server_default="voice")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
