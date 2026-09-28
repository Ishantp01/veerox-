"""Client-deployment-side cached licence validation result.

Replaces the earlier signed-JWT-assertion design with direct
authenticated, server-to-server HTTPS validation: this module calls the
owner's `GET /platform/license` (bearer-token authenticated, see
core/deployment_auth.py) and caches the structured JSON result — there is
no local signature to verify; trust comes from HTTPS + the bearer token +
validating the response shape/identity, not cryptography embedded in the
payload.

Cache lifetime is a fixed CODE CONSTANT (`CACHE_TTL_SECONDS`, 5 minutes),
not configurable via environment — the spec is explicit that this must not
be an env var. A cached entry's real expiry is always
`min(fetched_at + CACHE_TTL_SECONDS, licence's own expires_at)`, so caching
can never extend access past the actual licence.

Refresh coordination:
  * `refresh()` coalesces concurrent callers onto the SAME in-flight
    `asyncio.Task` (`self._inflight`) rather than merely serializing N
    separate outbound calls one after another — a second caller that
    arrives while a refresh is already running awaits that same task and
    gets its result, with no second HTTP request made at all.
  * A successful refresh ALWAYS overwrites the cache, whatever it says —
    including an unfavorable result (suspended/revoked/wrong identity), so
    "an explicit invalid licence or rejected/revoked credential must
    immediately clear cached permission" falls out of "the newest
    successful result always wins".
  * A FAILED refresh (network error, timeout, non-2xx, malformed JSON,
    identity mismatch) never touches the existing cache entry at all — it
    is not "extended", not cleared, just left exactly as it was. Its own
    expiry is what eventually makes `get_cached_result()` start returning
    None — there is no offline grace period on top of that.
  * `next_refresh_jitter_seconds()` spreads scheduled refreshes across
    replicas so they don't all hit the owner API in the same instant. True
    cross-replica coordination (so replica A's refresh also warms replica
    B's cache) is NOT implemented — each process keeps its own in-memory
    cache; see docs/multi-tenant-licensing.md's known limitations.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)

# Fixed by design (spec: "a five-minute cache duration defined as a code
# constant, not an environment variable") — do not move this to Settings.
CACHE_TTL_SECONDS = 300
# Defense in depth against a misbehaving/compromised owner endpoint or a
# hung connection — a validation call must never block a request thread
# indefinitely.
_REQUEST_TIMEOUT_SECONDS = 10.0


class LicenseValidationError(Exception):
    """The owner's response was unusable: a network/HTTP failure, malformed
    JSON, or an identity mismatch (the response names a different org than
    this deployment is configured for). Callers treat this exactly like "no
    result" — the existing cache, if any, is left untouched (see module
    docstring)."""


@dataclass(frozen=True)
class LicenseValidationResult:
    central_org_ref: str
    license_status: str
    valid_from: datetime | None
    license_expires_at: datetime | None
    enabled_features: list[str] | None
    config_version: int


@dataclass
class _CacheEntry:
    result: LicenseValidationResult
    expires_at: datetime  # min(fetched_at + CACHE_TTL_SECONDS, license_expires_at)


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _parse_validation_response(body: dict) -> LicenseValidationResult:
    try:
        organization = body["organization"]
        license_ = body["license"]
        return LicenseValidationResult(
            central_org_ref=organization["central_org_ref"],
            license_status=license_["status"],
            valid_from=_parse_datetime(license_.get("valid_from")),
            license_expires_at=_parse_datetime(license_.get("expires_at")),
            enabled_features=body.get("enabled_features"),
            config_version=body["config_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseValidationError(f"Malformed licence validation response: {exc}") from exc


class LicenseValidationCache:
    def __init__(self) -> None:
        self._entry: _CacheEntry | None = None
        self._inflight: asyncio.Task[LicenseValidationResult | None] | None = None

    def get_cache_expiry(self) -> datetime | None:
        """When the current cache entry stops being trusted, regardless of
        whether it's still there — used by the "why am I blocked" status
        endpoint (routers/provisioning.py), never for enforcement itself."""
        return self._entry.expires_at if self._entry else None

    def get_cached_result(self) -> LicenseValidationResult | None:
        """Non-blocking read for every protected request — checked on every
        call, never itself performs network I/O. Returns None if there is
        no cached result, or if it has expired (which, per the module
        docstring, is the point at which "block protected operations until
        validation succeeds" takes effect — there is no fallback beyond
        this)."""
        if self._entry is None:
            return None
        if self._entry.expires_at <= datetime.now(UTC):
            return None
        return self._entry.result

    async def refresh(self) -> LicenseValidationResult | None:
        """Coalesced call to the owner's licence API: if a refresh is
        already in flight, every concurrent caller awaits that SAME task
        rather than each firing its own outbound request — this is what
        "coalesce concurrent refresh requests" means literally (not merely
        serializing N separate calls one after another). Returns the fresh
        result on success. On any failure, leaves the existing cache entry
        completely untouched and returns whatever is still cached (which
        may itself already be expired — never resurrected by a failed
        refresh attempt)."""
        if self._inflight is not None:
            return await self._inflight

        task = asyncio.ensure_future(self._do_refresh())
        self._inflight = task
        try:
            return await task
        finally:
            self._inflight = None

    async def _do_refresh(self) -> LicenseValidationResult | None:
        try:
            result = await self._validate_with_owner()
        except Exception as exc:  # noqa: BLE001 - any failure just means "stay on the existing cache"
            logger.warning("license_validation_refresh_failed", error=str(exc))
            return self._entry.result if self._entry else None

        expires_at = datetime.now(UTC) + timedelta(seconds=CACHE_TTL_SECONDS)
        if result.license_expires_at is not None:
            expires_at = min(expires_at, result.license_expires_at)
        self._entry = _CacheEntry(result=result, expires_at=expires_at)
        return result

    async def _validate_with_owner(self) -> LicenseValidationResult:
        if not settings.license_api_url or not settings.license_api_token:
            raise LicenseValidationError(
                "LICENSE_API_URL/LICENSE_API_TOKEN must be set in client mode"
            )
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS,
            # Never forward the bearer token to a redirected host — a
            # malicious/misconfigured redirect at the owner's URL must not
            # be able to harvest this deployment's credential.
            follow_redirects=False,
        ) as http:
            try:
                response = await http.get(
                    f"{settings.license_api_url.rstrip('/')}/platform/license",
                    headers={"Authorization": f"Bearer {settings.license_api_token}"},
                )
            except httpx.HTTPError as exc:
                raise LicenseValidationError(f"Owner API unreachable: {exc}") from exc

            if response.status_code != 200:
                raise LicenseValidationError(
                    f"Owner API returned HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise LicenseValidationError(f"Owner API returned non-JSON body: {exc}") from exc

        result = _parse_validation_response(body)
        if result.central_org_ref != settings.default_org_id:
            raise LicenseValidationError(
                f"Owner API returned org {result.central_org_ref!r}, "
                f"this deployment is configured for {settings.default_org_id!r}"
            )
        return result


def next_refresh_jitter_seconds() -> float:
    """+/-20% jitter around the fixed cache lifetime, so many replicas of
    the same client deployment don't all refresh in lockstep."""
    return CACHE_TTL_SECONDS * random.uniform(0.8, 1.0)


_cache = LicenseValidationCache()


def get_license_cache() -> LicenseValidationCache:
    return _cache
