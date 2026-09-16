"""Tests for core/org_credentials.py's resolve_plivo_credentials /
resolve_twilio_credentials / resolve_meta_credentials — no platform-wide
fallback for a regular client org: a None/partially-configured org must
resolve to None, same as "not configured" (unlike core/org_openai_key.py's
fallback-to-platform-key behavior). The ONE exception is the platform's own
owner org (settings.default_org_id), covered separately below.

Deliberately uses an ORG_ID that does NOT match settings.default_org_id
(which is itself "00000000-0000-0000-0000-000000000001" in this repo's real
.env, loaded into the `settings` singleton even under pytest) — otherwise
every "not configured" case here would silently pick up the owner-org env
fallback and these tests would pass/fail depending on what's in .env."""

from __future__ import annotations

import uuid

import pytest

from apps.api.config import settings
from apps.api.core.crypto import encrypt_secret
from apps.api.core.org_credentials import (
    resolve_meta_credentials,
    resolve_plivo_credentials,
    resolve_twilio_credentials,
)
from apps.api.db.models.org import Org

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
assert str(ORG_ID) != settings.default_org_id


def _org(**overrides: object) -> Org:
    return Org(id=ORG_ID, name="Test Org", **overrides)


def test_resolve_plivo_credentials_none_org() -> None:
    assert resolve_plivo_credentials(None) is None


def test_resolve_plivo_credentials_not_configured() -> None:
    assert resolve_plivo_credentials(_org()) is None


def test_resolve_plivo_credentials_partially_configured() -> None:
    """auth_id with no token, or vice versa, is not configured."""
    assert resolve_plivo_credentials(_org(plivo_auth_id="id-only")) is None
    assert (
        resolve_plivo_credentials(_org(plivo_auth_token_encrypted=encrypt_secret("token-only")))
        is None
    )


def test_resolve_plivo_credentials_configured() -> None:
    org = _org(plivo_auth_id="my-auth-id", plivo_auth_token_encrypted=encrypt_secret("my-token"))
    creds = resolve_plivo_credentials(org)
    assert creds is not None
    assert creds.auth_id == "my-auth-id"
    assert creds.auth_token == "my-token"


def test_resolve_plivo_credentials_decrypt_failure_treated_as_not_configured() -> None:
    org = _org(plivo_auth_id="my-auth-id", plivo_auth_token_encrypted="not-a-real-fernet-token")
    assert resolve_plivo_credentials(org) is None


def test_resolve_twilio_credentials_not_configured() -> None:
    assert resolve_twilio_credentials(_org()) is None


def test_resolve_twilio_credentials_configured() -> None:
    org = _org(
        twilio_account_sid="AC123", twilio_auth_token_encrypted=encrypt_secret("twilio-token")
    )
    creds = resolve_twilio_credentials(org)
    assert creds is not None
    assert creds.account_sid == "AC123"
    assert creds.auth_token == "twilio-token"


def test_resolve_twilio_credentials_decrypt_failure_treated_as_not_configured() -> None:
    org = _org(twilio_account_sid="AC123", twilio_auth_token_encrypted="garbage")
    assert resolve_twilio_credentials(org) is None


def test_resolve_meta_credentials_not_configured() -> None:
    assert resolve_meta_credentials(_org()) is None


def test_resolve_meta_credentials_partially_configured_missing_access_token() -> None:
    org = _org(
        meta_app_id="app-1",
        meta_app_secret_encrypted=encrypt_secret("secret"),
    )
    assert resolve_meta_credentials(org) is None


def test_resolve_meta_credentials_configured_without_business_account_or_verify_token() -> None:
    """business_account_id / verify_token are optional — an org can send
    once app_id + app_secret + access_token are set."""
    org = _org(
        meta_app_id="app-1",
        meta_app_secret_encrypted=encrypt_secret("secret"),
        meta_access_token_encrypted=encrypt_secret("token"),
    )
    creds = resolve_meta_credentials(org)
    assert creds is not None
    assert creds.app_id == "app-1"
    assert creds.app_secret == "secret"
    assert creds.access_token == "token"
    assert creds.business_account_id is None
    assert creds.verify_token is None


def test_resolve_meta_credentials_fully_configured() -> None:
    org = _org(
        meta_app_id="app-1",
        meta_app_secret_encrypted=encrypt_secret("secret"),
        meta_access_token_encrypted=encrypt_secret("token"),
        meta_whatsapp_business_account_id="waba-1",
        meta_verify_token_encrypted=encrypt_secret("verify-me"),
    )
    creds = resolve_meta_credentials(org)
    assert creds is not None
    assert creds.business_account_id == "waba-1"
    assert creds.verify_token == "verify-me"


def test_resolve_meta_credentials_decrypt_failure_treated_as_not_configured() -> None:
    org = _org(
        meta_app_id="app-1",
        meta_app_secret_encrypted="garbage",
        meta_access_token_encrypted=encrypt_secret("token"),
    )
    assert resolve_meta_credentials(org) is None


# ---------------------------------------------------------------------------
# Owner-org fallback — settings.default_org_id is the ONE org allowed to
# fall back to the platform-wide settings.plivo_*/twilio_*/meta_* env vars
# when it hasn't saved its own in the DB (see core/org_credentials.py's
# module docstring). Every case above uses a non-owner ORG_ID specifically
# so it never accidentally exercises this path.
# ---------------------------------------------------------------------------


def _owner_org(**overrides: object) -> Org:
    return Org(id=uuid.UUID(settings.default_org_id), name="Owner Org", **overrides)


def test_owner_org_falls_back_to_env_plivo_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "plivo_auth_id", "env-auth-id")
    monkeypatch.setattr(settings, "plivo_auth_token", "env-auth-token")
    creds = resolve_plivo_credentials(_owner_org())
    assert creds is not None
    assert creds.auth_id == "env-auth-id"
    assert creds.auth_token == "env-auth-token"


def test_owner_org_db_credentials_take_priority_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "plivo_auth_id", "env-auth-id")
    monkeypatch.setattr(settings, "plivo_auth_token", "env-auth-token")
    org = _owner_org(plivo_auth_id="db-auth-id", plivo_auth_token_encrypted=encrypt_secret("db-token"))
    creds = resolve_plivo_credentials(org)
    assert creds is not None
    assert creds.auth_id == "db-auth-id"
    assert creds.auth_token == "db-token"


def test_owner_org_with_no_env_credentials_either_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "plivo_auth_id", None)
    monkeypatch.setattr(settings, "plivo_auth_token", None)
    assert resolve_plivo_credentials(_owner_org()) is None


def test_non_owner_org_never_falls_back_to_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "plivo_auth_id", "env-auth-id")
    monkeypatch.setattr(settings, "plivo_auth_token", "env-auth-token")
    assert resolve_plivo_credentials(_org()) is None


def test_owner_org_falls_back_to_env_twilio_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "twilio_account_sid", "env-sid")
    monkeypatch.setattr(settings, "twilio_auth_token", "env-token")
    creds = resolve_twilio_credentials(_owner_org())
    assert creds is not None
    assert creds.account_sid == "env-sid"
    assert creds.auth_token == "env-token"


def test_owner_org_falls_back_to_env_meta_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "meta_app_id", "env-app-id")
    monkeypatch.setattr(settings, "meta_app_secret", "env-app-secret")
    monkeypatch.setattr(settings, "meta_access_token", "env-access-token")
    monkeypatch.setattr(settings, "meta_whatsapp_business_account_id", "env-waba")
    monkeypatch.setattr(settings, "meta_verify_token", "env-verify-token")
    creds = resolve_meta_credentials(_owner_org())
    assert creds is not None
    assert creds.app_id == "env-app-id"
    assert creds.app_secret == "env-app-secret"
    assert creds.access_token == "env-access-token"
    assert creds.business_account_id == "env-waba"
    assert creds.verify_token == "env-verify-token"
