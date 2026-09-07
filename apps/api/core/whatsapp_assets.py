"""Org WhatsApp media library — lookup + prompt helpers.

A leaf module (imports only the model + settings) shared by the agent tool
(``core/tools.py::send_whatsapp_file``) and both system-prompt builders
(``core/agent.py`` and ``channels/voice/realtime_bridge.py``), so the agent
knows which files it can send and can resolve the one a contact asked for.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.whatsapp_asset import WhatsAppAsset

# Meta caps: image 5 MB, video 16 MB, document 100 MB. We take the smallest
# common ceiling for uploads — keeps Postgres rows sane and covers the
# price-list / brochure / short-clip use case.
MAX_ASSET_BYTES = 16 * 1024 * 1024

# content-type prefix -> Meta media type. Anything else is sent as a document.
_IMAGE_PREFIX = "image/"
_VIDEO_PREFIX = "video/"


def media_type_for_mime(mime: str | None) -> str:
    """Map an uploaded file's content-type to the Meta message ``type``."""
    m = (mime or "").lower()
    if m.startswith(_IMAGE_PREFIX):
        return "image"
    if m.startswith(_VIDEO_PREFIX):
        return "video"
    return "document"


def asset_public_url(asset: WhatsAppAsset) -> str:
    """The public URL Meta fetches the file from (see routers/media.py).

    ``PUBLIC_BASE_URL`` must be the real deployed origin — Meta pulls this
    server-side, exactly like the WhatsApp webhook URL.
    """
    return (
        f"{settings.public_base_url.rstrip('/')}"
        f"/media/wa-asset/{asset.id}?k={asset.access_key}"
    )


async def load_org_assets(db: AsyncSession, org_id: UUID) -> list[WhatsAppAsset]:
    result = await db.execute(
        select(WhatsAppAsset)
        .where(WhatsAppAsset.org_id == org_id)
        .order_by(WhatsAppAsset.created_at)
    )
    return list(result.scalars().all())


async def resolve_asset(
    db: AsyncSession, org_id: UUID, name: str
) -> WhatsAppAsset | list[str] | None:
    """Find the asset the agent named.

    Returns the ``WhatsAppAsset`` on a confident match, a ``list[str]`` of
    candidate names when the name is ambiguous, or ``None`` when nothing
    matches. Tries an exact case-insensitive name first, then a single
    substring match.
    """
    wanted = (name or "").strip()
    if not wanted:
        return None

    exact = (
        await db.execute(
            select(WhatsAppAsset).where(
                WhatsAppAsset.org_id == org_id,
                func.lower(WhatsAppAsset.name) == wanted.lower(),
            )
        )
    ).scalars().all()
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return [a.name for a in exact]

    partial = (
        await db.execute(
            select(WhatsAppAsset).where(
                WhatsAppAsset.org_id == org_id,
                func.lower(WhatsAppAsset.name).like(f"%{wanted.lower()}%"),
            )
        )
    ).scalars().all()
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        return [a.name for a in partial]
    return None


async def asset_catalog_prompt_block(db: AsyncSession, org_id: UUID) -> str:
    """A short system-prompt block listing the files the agent may send, or
    ``""`` when the org has uploaded none."""
    assets = await load_org_assets(db, org_id)
    if not assets:
        return ""
    lines = [
        "Files you can send to the contact over WhatsApp with the "
        "send_whatsapp_file tool (pass the name exactly as written here):"
    ]
    for a in assets:
        desc = f" — {a.description.strip()}" if a.description and a.description.strip() else ""
        lines.append(f'- "{a.name}" ({a.media_type}){desc}')
    lines.append(
        "Only send a file the contact actually asked for, or one that directly "
        "answers their question. Never name a file that isn't in this list."
    )
    return "\n".join(lines)
