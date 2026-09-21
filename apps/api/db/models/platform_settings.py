from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class PlatformSettings(Base):
    """Singleton row (id fixed at 1) holding platform-wide, admin-editable
    settings that aren't tied to any one Org or Plan — the social-media
    links shown to every client org."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # e.g. {"twitter": "https://...", "linkedin": "https://..."} — only
    # non-empty entries are shown to clients.
    social_links: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
