from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class PlatformSettings(Base):
    """Singleton row (id fixed at 1) holding platform-wide, admin-editable
    settings that aren't tied to any one Org or Plan — the help-desk
    chatbot's system script and the social-media links shown to every
    client org."""

    __tablename__ = "platform_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # NULL means "use the built-in HELP_DESK_SYSTEM_PROMPT default" (see
    # core/prompts.py), same NULL-means-default convention as Org.script.
    help_desk_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. {"twitter": "https://...", "linkedin": "https://..."} — only
    # non-empty entries are shown to clients.
    social_links: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
