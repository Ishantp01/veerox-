"""core/license_cache.py — the client-side direct-HTTPS licence validation
cache: fixed 5-minute TTL (capped at the licence's own expiry), coalesced
refreshes, and the specific rule that a FAILED refresh never touches the
existing cache (no extension, no clearing) while a SUCCESSFUL refresh
always overwrites it outright, however unfavorable."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.api.config import settings
from apps.api.core import license_cache as license_cache_module
from apps.api.core.license_cache import CACHE_TTL_SECONDS, LicenseValidationCache


@pytest.fixture(autouse=True)
def _client_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "license_api_url", "https://owner.example")
    monkeypatch.setattr(settings, "license_api_token", "test-token")
    monkeypatch.setattr(settings, "default_org_id", "00000000-0000-0000-0000-000000000001")


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or (str(body) if body else "")

    def json(self) -> dict:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient — call_log records every request
    made so a test can assert coalescing collapsed concurrent refreshes into
    a single outbound call."""

    call_log: list[str] = []

    def __init__(self, response: _FakeResponse | Exception, *, delay: float = 0.0) -> None:
        self._response = response
        self._delay = delay

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str, headers: dict) -> _FakeResponse:
        _FakeAsyncClient.call_log.append(url)
        if self._delay:
            await asyncio.sleep(self._delay)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_http(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | Exception, *, delay: float = 0.0) -> None:
    _FakeAsyncClient.call_log = []
    monkeypatch.setattr(
        license_cache_module.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(response, delay=delay)
    )


def _valid_body(*, status: str = "active", expires_at: str | None = None, config_version: int = 1) -> dict:
    return {
        "organization": {"central_org_ref": "00000000-0000-0000-0000-000000000001", "name": "Acme"},
        "deployment_id": "dep-1",
        "license": {"status": status, "valid_from": None, "expires_at": expires_at},
        "enabled_features": ["crm"],
        "config_version": config_version,
    }


async def test_successful_refresh_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body()))
    cache = LicenseValidationCache()
    result = await cache.refresh()
    assert result is not None
    assert result.license_status == "active"
    assert cache.get_cached_result() is not None


async def test_cache_expiry_never_exceeds_actual_license_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    soon = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body(expires_at=soon)))
    cache = LicenseValidationCache()
    await cache.refresh()
    expiry = cache.get_cache_expiry()
    assert expiry is not None
    assert expiry <= datetime.now(UTC) + timedelta(seconds=CACHE_TTL_SECONDS)
    assert (expiry - datetime.now(UTC)).total_seconds() < 5


async def test_failed_refresh_leaves_existing_cache_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body()))
    cache = LicenseValidationCache()
    first = await cache.refresh()
    expiry_after_success = cache.get_cache_expiry()

    _patch_http(monkeypatch, httpx.ConnectError("network down"))
    second = await cache.refresh()

    assert second == first  # unchanged, not cleared
    assert cache.get_cache_expiry() == expiry_after_success  # not extended either


async def test_failed_refresh_never_resurrects_an_already_expired_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body()))
    cache = LicenseValidationCache()
    await cache.refresh()
    cache._entry.expires_at = datetime.now(UTC) - timedelta(seconds=1)  # force expiry

    _patch_http(monkeypatch, httpx.ConnectError("network down"))
    await cache.refresh()

    assert cache.get_cached_result() is None  # still blocked — no offline grace


async def test_successful_unfavorable_refresh_overwrites_favorable_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body(status="active")))
    cache = LicenseValidationCache()
    await cache.refresh()
    assert cache.get_cached_result().license_status == "active"

    _patch_http(monkeypatch, _FakeResponse(200, _valid_body(status="revoked")))
    await cache.refresh()
    assert cache.get_cached_result().license_status == "revoked"


async def test_malformed_response_is_treated_as_a_failed_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, {"unexpected": "shape"}))
    cache = LicenseValidationCache()
    result = await cache.refresh()
    assert result is None
    assert cache.get_cached_result() is None


async def test_identity_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _valid_body()
    body["organization"]["central_org_ref"] = "99999999-9999-9999-9999-999999999999"
    _patch_http(monkeypatch, _FakeResponse(200, body))
    cache = LicenseValidationCache()
    result = await cache.refresh()
    assert result is None
    assert cache.get_cached_result() is None


async def test_non_200_is_treated_as_a_failed_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(403, text="Forbidden"))
    cache = LicenseValidationCache()
    result = await cache.refresh()
    assert result is None


async def test_concurrent_refreshes_coalesce_into_one_outbound_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_http(monkeypatch, _FakeResponse(200, _valid_body()), delay=0.05)
    cache = LicenseValidationCache()
    results = await asyncio.gather(cache.refresh(), cache.refresh(), cache.refresh())
    assert all(r is not None for r in results)
    assert len(_FakeAsyncClient.call_log) == 1
