"""Background worker that auto-flips an org's license from "active" to
"expired" once its `license_expires_at` has passed.

Runs as an in-process ``asyncio.create_task`` from the FastAPI lifespan (see
``apps/api/main.py``), mirroring ``workers/follow_up_dispatcher.py``'s
poll-loop structure. A manual "suspended" status is never touched here —
only the admin's own POST /billing/orgs/{id}/license/suspend|reactivate
actions change that.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from sqlalchemy import update

from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 300


async def _expire_due_licenses() -> None:
    async with AsyncSessionLocal() as db:
        stmt = (
            update(Org)
            .where(Org.license_status == "active", Org.license_expires_at < datetime.now(UTC))
            .values(license_status="expired")
        )
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount:
            logger.info("license_expiry_worker_expired_orgs", count=result.rowcount)


async def run_license_expiry_worker() -> None:
    """The worker's main loop — runs for the lifetime of the app process."""
    while True:
        try:
            await _expire_due_licenses()
        except Exception:  # noqa: BLE001
            logger.exception("license_expiry_worker_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
