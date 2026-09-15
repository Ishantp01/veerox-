from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class QualificationCriteriaPreset(Base):
    """One named, reusable qualification bar an org can pick from a dropdown
    when creating a campaign (see routers/admin.py's create_campaign), instead
    of retyping the criteria text every time. ``criteria_text`` is copied
    verbatim into CallCampaign.criteria at creation time — editing or
    deleting a preset later never changes campaigns already created from it.
    """

    __tablename__ = "qualification_criteria_presets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    criteria_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
