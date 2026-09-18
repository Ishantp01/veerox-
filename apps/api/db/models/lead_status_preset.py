from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class LeadStatusPreset(Base):
    """A custom pipeline stage an org has added on top of the built-in
    new/contacted/qualified/converted/lost statuses (see LEAD_STATUSES in
    schemas/lead.py). ``name`` is stored and used verbatim as the value
    written to Lead.status — there's no separate slug/label split, mirroring
    how the built-in statuses are also just plain strings on that column.
    Deleting a preset doesn't touch leads already set to that status; it just
    stops offering it in the dropdown for new picks.
    """

    __tablename__ = "lead_status_presets"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_lead_status_presets_org_id_name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
