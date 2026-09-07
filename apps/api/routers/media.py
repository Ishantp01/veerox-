"""Public media serving — the WhatsApp agent's uploaded assets.

Meta fetches ``image``/``document``/``video`` messages server-side from a
plain HTTPS URL, so this route has **no auth guard**. The file id is a UUID
and the URL also carries ``?k=<access_key>`` (a random per-asset token) —
both must match, so a bare id guess leaks nothing. See
``db/models/whatsapp_asset.py`` and ``core/whatsapp_assets.py``.
"""

from __future__ import annotations

import secrets
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from apps.api.db.models.whatsapp_asset import WhatsAppAsset
from apps.api.deps import DbDep

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["media"])


@router.get("/media/wa-asset/{asset_id}")
async def serve_wa_asset(asset_id: UUID, k: str, db: DbDep) -> Response:
    asset = await db.get(WhatsAppAsset, asset_id)
    if asset is None or not secrets.compare_digest(k, asset.access_key):
        # Same 404 for "no such asset" and "wrong key" — don't confirm the id.
        raise HTTPException(status_code=404, detail="Not found")

    headers = {"Cache-Control": "private, max-age=300"}
    if asset.media_type == "document":
        headers["Content-Disposition"] = f'inline; filename="{asset.filename}"'
    return Response(content=asset.data, media_type=asset.mime_type, headers=headers)
