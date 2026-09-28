"""Client-deployment-mode enforcement: the cached direct-HTTPS licence
validation result — not the local Org row — is the source of truth for
`is_org_license_active`/`is_org_feature_enabled` once
`settings.deployment_mode == "client"`, and a confirmed suspension/
revocation invalidates cached access as soon as the next refresh lands."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from apps.api.config import settings
from apps.api.core.license_cache import LicenseValidationResult, _CacheEntry, get_license_cache
from apps.api.deps import is_org_feature_enabled, is_org_license_active


@pytest.fixture(autouse=True)
def _client_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "deployment_mode", "client")
    cache = get_license_cache()
    cache._entry = None
    yield
    cache._entry = None


def _set_cached_result(*, license_status: str, enabled_features: list[str] | None, expires_in: timedelta) -> None:
    result = LicenseValidationResult(
        central_org_ref="00000000-0000-0000-0000-000000000001",
        license_status=license_status,
        valid_from=None,
        license_expires_at=None,
        enabled_features=enabled_features,
        config_version=1,
    )
    get_license_cache()._entry = _CacheEntry(result=result, expires_at=datetime.now(UTC) + expires_in)


async def test_no_cached_result_blocks_access(db_session) -> None:
    assert await is_org_license_active(db_session, uuid.UUID(int=1)) is False


async def test_active_cached_result_allows_access(db_session) -> None:
    _set_cached_result(license_status="active", enabled_features=None, expires_in=timedelta(minutes=5))
    assert await is_org_license_active(db_session, uuid.UUID(int=1)) is True


async def test_suspended_cached_result_blocks_access_even_if_unexpired(db_session) -> None:
    """A confirmed suspension invalidates cached access immediately — an
    older favorable result is never trusted once a newer, unfavorable one
    has been cached."""
    _set_cached_result(license_status="suspended", enabled_features=None, expires_in=timedelta(minutes=5))
    assert await is_org_license_active(db_session, uuid.UUID(int=1)) is False


async def test_expired_cached_result_blocks_access(db_session) -> None:
    _set_cached_result(license_status="active", enabled_features=None, expires_in=timedelta(seconds=-1))
    assert await is_org_license_active(db_session, uuid.UUID(int=1)) is False


async def test_feature_restriction_comes_from_cached_result(db_session) -> None:
    _set_cached_result(license_status="active", enabled_features=["crm"], expires_in=timedelta(minutes=5))
    assert await is_org_feature_enabled(db_session, uuid.UUID(int=1), "crm") is True
    assert await is_org_feature_enabled(db_session, uuid.UUID(int=1), "leads") is False


async def test_null_enabled_features_means_unrestricted(db_session) -> None:
    _set_cached_result(license_status="active", enabled_features=None, expires_in=timedelta(minutes=5))
    assert await is_org_feature_enabled(db_session, uuid.UUID(int=1), "anything") is True
