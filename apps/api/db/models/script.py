from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class Script(Base):
    """One named AI-calling script an org can pick per campaign (see
    db/models/call_campaign.py's script_id) or leave as the org's default,
    used whenever a campaign doesn't pick one — see
    channels/voice/realtime_bridge.py::_system_instructions for the
    resolution order. Voice-only: WhatsApp still uses the single
    Org.script column (see db/models/org.py), untouched by this table.

    Exactly one row per org is expected to carry is_default=True.
    Enforced in application code, not a DB constraint — same reasoning as
    OrgPhoneNumber.is_default (a partial unique index doesn't translate to
    the SQLite backend the test suite runs against).
    """

    __tablename__ = "scripts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
