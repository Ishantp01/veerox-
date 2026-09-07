from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base


class WhatsAppAsset(Base):
    """One file an org has uploaded for its WhatsApp agent to send to a
    contact — a price list PDF, a brochure image, a short product video, etc.

    The agent picks one by ``name`` (see core/whatsapp_assets.py::resolve_asset)
    when a contact asks for that information, and sends it via the
    ``send_whatsapp_file`` tool. Bytes live in ``data`` rather than object
    storage — Veerox has no blob store, and these files are small (uploads are
    capped at 16 MB). Meta fetches the file server-side from the public
    ``/media/wa-asset/{id}?k={access_key}`` route, so ``access_key`` (a random
    token, required on that URL) is what keeps a bare id guess from leaking a
    file.

    Org-scoped exactly like ``Script`` — cascade-deleted with the org.
    """

    __tablename__ = "whatsapp_assets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # One of "document" | "image" | "video" — the Meta message ``type``.
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    access_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
