"""Client-deployment-side licence-status + recheck endpoints (spec §5, §8).
Only meaningful when `settings.deployment_mode == "client"`.

Deliberately NOT gated by `enforce_org_license`/`require_feature` — these
are exactly the "narrowly scoped ... licence-status, provisioning, and
recovery endpoints" that must stay reachable even while a licence is
invalid, so the dashboard can always explain WHY it's blocked instead of
just failing every other call with an opaque 403, and so renewal can be
rechecked without a chicken-and-egg lockout.

There is no inbound write endpoint here — provisioning changes are applied
in-process by workers/central_sync_worker.py after it authenticates its own
pull from the owner API (core/provisioning_apply.py). Removing the earlier
signed-assertion design also removed the shared secret that used to
authenticate an inbound push from the owner; rather than inventing a new
credential for that direction, delivery is now client-pull-only end to end.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from apps.api.core.license_cache import get_license_cache
from apps.api.rate_limit import limiter

router = APIRouter(prefix="/provisioning", tags=["provisioning"])


def _license_status_body() -> dict:
    cache = get_license_cache()
    result = cache.get_cached_result()
    cache_expires_at = cache.get_cache_expiry()
    if result is None:
        return {
            "status": "setup_pending",
            "message": (
                "This deployment has not completed licence validation yet — it is retrying "
                "automatically. No business access is granted until validation succeeds."
            ),
            "checked_at": datetime.now(UTC).isoformat(),
        }
    return {
        "status": result.license_status,
        "enabled_features": result.enabled_features,
        "expires_at": result.license_expires_at.isoformat() if result.license_expires_at else None,
        "cache_expires_at": cache_expires_at.isoformat() if cache_expires_at else None,
        "config_version": result.config_version,
        "checked_at": datetime.now(UTC).isoformat(),
    }


@router.get("/license-status")
async def license_status() -> dict:
    """Backs this client deployment's "restricted licence" screen."""
    return _license_status_body()


@router.post("/recheck")
@limiter.limit("3/minute")
async def recheck_license(request: Request) -> dict:
    """Manual "check now" action so a renewal can restore access promptly
    instead of waiting out the full 5-minute cache — rate-limited so a
    dashboard user mashing the button can't turn this into an amplified
    load source against the owner API."""
    await get_license_cache().refresh()
    return _license_status_body()
