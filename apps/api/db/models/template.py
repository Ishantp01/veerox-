from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class WhatsAppTemplate(Base):
    """A saved Meta-approved WhatsApp template, so admins pick a template from
    a dropdown on the send form instead of retyping its name/language and
    ordered body params every time.
    """

    __tablename__ = "whatsapp_templates"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, server_default="en_US")
    category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Ordered labels for the template body's {{1}}, {{2}}, ... placeholders;
    # length determines how many param inputs the send form renders.
    param_labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
