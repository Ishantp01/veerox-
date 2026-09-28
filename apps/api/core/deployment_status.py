"""Owner-panel-facing connection status for an org's client deployment —
one of exactly four states, chosen so a non-technical reader of the owner
panel can tell what's going on without needing to know what a "bearer
token" or "config version" is:

  "awaiting_client_setup" — a token exists but this deployment has never
      successfully authenticated to the owner API yet (or, for an org
      created before this feature existed, no deployment/token has been
      prepared at all).
  "connected"              — has authenticated AND acknowledged the org's
      current configuration. Never shown merely because a token was
      generated — that alone proves nothing about whether the client is
      actually running.
  "sync_pending"            — has authenticated, but hasn't (yet)
      acknowledged the org's latest configuration version, or its last
      sync attempt failed.
  "connection_issue"        — the deployment's credential was revoked, or
      it authenticated before but hasn't checked in for an unusually long
      time (longer than several validation-cache cycles), suggesting it's
      down or misconfigured rather than merely between polls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apps.api.core.license_cache import CACHE_TTL_SECONDS
from apps.api.db.models.client_deployment import ClientDeployment
from apps.api.db.models.org import Org

# A healthy client refreshes roughly every CACHE_TTL_SECONDS (jittered). No
# check-in for several multiples of that is a real signal something's
# wrong, not just "between polls" — three full cycles gives comfortable
# margin against jitter and an occasional slow tick.
_STALE_AFTER_SECONDS = CACHE_TTL_SECONDS * 3

CONNECTION_STATUSES = ("awaiting_client_setup", "connected", "sync_pending", "connection_issue")


def compute_connection_status(deployment: ClientDeployment | None, org: Org, *, now: datetime | None = None) -> str:
    if deployment is None:
        return "awaiting_client_setup"

    if deployment.status == "revoked":
        return "connection_issue"
    if deployment.status == "pending":
        return "awaiting_client_setup"

    now = now or datetime.now(UTC)
    last_seen_at = deployment.last_seen_at
    if last_seen_at is not None:
        last_seen_at = last_seen_at if last_seen_at.tzinfo is not None else last_seen_at.replace(tzinfo=UTC)
    if last_seen_at is not None and now - last_seen_at > timedelta(seconds=_STALE_AFTER_SECONDS):
        return "connection_issue"

    if deployment.config_version < org.config_version or deployment.last_sync_status != "synced":
        return "sync_pending"

    return "connected"
