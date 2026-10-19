"""Clears stored call-recording references past each org's configured retention.

Off unless VOICE_RETENTION_WORKER_ENABLED is set. Only orgs that configured ``retention_days`` in their
voice compliance policy are touched, and only the reference we store (URL + duration): the audio is
hosted by the telephony provider and is not deleted from here. Runs once a day.
"""

from __future__ import annotations

import asyncio

import structlog

from apps.api.channels.voice.compliance import purge_expired_recordings
from apps.api.config import settings
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_INTERVAL_SECS = 24 * 60 * 60


async def purge_once() -> int:
    async with AsyncSessionLocal() as db:
        return await purge_expired_recordings(db)


async def run_voice_retention_worker() -> None:
    """Main loop; sleeps forever (cheaply) when the worker is not enabled."""
    while True:
        if settings.voice_retention_worker_enabled:
            try:
                cleared = await purge_once()
                if cleared:
                    logger.info("voice_retention_cleared_recordings", count=cleared)
            except Exception:  # noqa: BLE001
                logger.exception("voice_retention_tick_failed")
                await record_error()
        await asyncio.sleep(_INTERVAL_SECS)
