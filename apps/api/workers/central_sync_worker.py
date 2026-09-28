"""Client-deployment-side background loop: keeps the licence validation
cache warm (core/license_cache.py) and pulls/applies any pending
organization/feature configuration change (core/provisioning_apply.py).
Runs only when `settings.deployment_mode == "client"` (see main.py's
lifespan).

Both concerns share one tick because they talk to the same owner API on the
same authenticated connection — there's no benefit to two separate loops.
Ticks are jittered (core/license_cache.py's next_refresh_jitter_seconds) so
many replicas of the same client deployment don't all call the owner API in
lockstep.

First start (or any restart with no cached validation yet) is a special
case: the app must stay in a restricted "setup pending" state — no business
access at all (deps.py's client-mode checks already enforce this; there is
no cached result to grant it) — and retry sooner than the normal ~5-minute
cadence, since an owner API outage during initial setup shouldn't leave a
freshly deployed client waiting a full cycle. See
`_FIRST_SUCCESS_RETRY_SECONDS`.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from apps.api.config import settings
from apps.api.core.license_cache import get_license_cache, next_refresh_jitter_seconds
from apps.api.core.provisioning_apply import apply_provisioning_locally
from apps.api.db.session import AsyncSessionLocal
from apps.api.redis_client import record_error

logger = structlog.get_logger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10.0
# Backoff sequence used ONLY until the very first successful validation —
# short and quick so a client doesn't sit in "setup pending" for a full
# ~5-minute cycle just because the owner API happened to be briefly
# unreachable at the exact moment of first startup. Repeats at the final
# value indefinitely — this never gives up, it just keeps retrying.
_FIRST_SUCCESS_RETRY_SECONDS = (5, 10, 20, 30, 60)


async def _pull_and_apply_pending_sync() -> None:
    if not settings.license_api_url or not settings.license_api_token:
        return

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS, follow_redirects=False) as http:
        response = await http.get(
            f"{settings.license_api_url.rstrip('/')}/platform/sync/pending",
            headers={"Authorization": f"Bearer {settings.license_api_token}"},
        )
        response.raise_for_status()
        body = response.json()
        if body is None:
            return  # nothing pending

        async with AsyncSessionLocal() as db:
            org, applied = await apply_provisioning_locally(db, body)
            _ = org

        if not applied:
            return  # stale/already-applied — nothing to ack

        ack_response = await http.post(
            f"{settings.license_api_url.rstrip('/')}/platform/sync/ack",
            headers={"Authorization": f"Bearer {settings.license_api_token}"},
            json={"config_version": body["config_version"]},
        )
        ack_response.raise_for_status()


async def run_central_sync_worker() -> None:
    cache = get_license_cache()

    # "Setup pending": keep retrying quickly until the FIRST successful
    # validation ever lands. No business access is granted in the
    # meantime — deps.py's client-mode checks block outright while
    # get_cached_result() is None, which stays true for as long as this
    # loop keeps failing.
    attempt = 0
    while cache.get_cached_result() is None:
        result = await cache.refresh()
        if result is not None:
            break
        delay = _FIRST_SUCCESS_RETRY_SECONDS[min(attempt, len(_FIRST_SUCCESS_RETRY_SECONDS) - 1)]
        logger.warning("central_sync_worker_awaiting_first_validation", retry_in_seconds=delay)
        await asyncio.sleep(delay)
        attempt += 1

    try:
        await _pull_and_apply_pending_sync()
    except Exception:  # noqa: BLE001
        logger.exception("central_sync_worker_initial_sync_failed")

    while True:
        await asyncio.sleep(next_refresh_jitter_seconds())
        try:
            await cache.refresh()
        except Exception:  # noqa: BLE001 - refresh() itself never raises; defensive only
            logger.exception("central_sync_worker_license_refresh_failed")
            await record_error()

        try:
            await _pull_and_apply_pending_sync()
        except Exception:  # noqa: BLE001
            logger.exception("central_sync_worker_pending_sync_failed")
            await record_error()
