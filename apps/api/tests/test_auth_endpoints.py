from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest_asyncio.fixture(autouse=True)
async def _stub_plivo_sms(monkeypatch: pytest.MonkeyPatch) -> None:
    """provision_org SMS's the login token via the real Plivo API — stub it
    so these tests never make a live network call. Individual tests can
    still override this via monkeypatch for their own scenarios.
    """
    from apps.api.routers import auth as auth_module

    async def _fake_send_sms(to_e164: str, text: str) -> tuple[dict, str]:
        return {"message_uuid": "fake"}, "plivo"

    monkeypatch.setattr(auth_module.voice_failover, "send_sms", _fake_send_sms)


@pytest_asyncio.fixture(autouse=True)
async def _stub_brevo_email(monkeypatch: pytest.MonkeyPatch) -> None:
    """forgot_token emails the new login token via the real Brevo API —
    stub it so these tests never make a live network call.
    """
    from apps.api.routers import auth as auth_module

    async def _fake_send_email(
        to_email: str, subject: str, html_content: str, to_name: str | None = None
    ) -> dict:
        return {"messageId": "fake"}

    monkeypatch.setattr(auth_module.brevo_client, "send_email", _fake_send_email)


async def _seed_org(db: AsyncSession) -> Org:
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.commit()
    return org


@pytest_asyncio.fixture
async def account_with_membership(db_session: AsyncSession) -> tuple[AccountUser, str]:
    org = await _seed_org(db_session)
    token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()
    return account, token


@pytest_asyncio.fixture
async def account_with_mobile(db_session: AsyncSession) -> tuple[AccountUser, str]:
    org = await _seed_org(db_session)
    token = generate_login_token()
    account = AccountUser(
        email="mobile-user@example.com", token_hash=hash_token(token), mobile="+919876500000"
    )
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()
    return account, token


async def test_provision_org_creates_org_user_and_membership(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/provision-org",
        json={"org_name": "Acme", "email": "founder@acme.com", "mobile": "+919876543210"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "founder@acme.com"
    assert body["login_token"]
    assert body["sms_sent"] is True


async def test_provision_org_requires_admin(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/provision-org",
        json={"org_name": "Acme", "email": "founder@acme.com", "mobile": "+919876543210"},
    )
    assert response.status_code == 403


async def test_provision_org_rejects_duplicate_email(
    client: AsyncClient, account_with_membership: tuple[AccountUser, str]
) -> None:
    response = await client.post(
        "/auth/provision-org",
        json={
            "org_name": "Another Org",
            "email": "admin@example.com",
            "mobile": "+919876543210",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 400


async def test_login_round_trips_through_get_current_user(
    client: AsyncClient, account_with_membership: tuple[AccountUser, str]
) -> None:
    _, token = account_with_membership
    login_response = await client.post("/auth/login", json={"token": token})
    assert login_response.status_code == 200
    session_token = login_response.json()["token"]

    me_response = await client.get("/auth/me", headers={"X-Session-Token": session_token})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "admin@example.com"
    assert me_response.json()["role"] == "admin"


async def test_admin_token_logs_into_default_org_owner(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No AccountUser/OrgMembership seeded here on purpose — POST /auth/login
    auto-provisions the default org's superuser account the first time the
    shared admin token is used to log in (see auth.py's
    _ensure_default_org_owner), so this only needs the default Org to exist.
    """
    default_org = Org(id=uuid.UUID(settings.default_org_id), name="Veerox Owner Org")
    db_session.add(default_org)
    await db_session.commit()

    login_response = await client.post("/auth/login", json={"token": settings.admin_token})
    assert login_response.status_code == 200
    body = login_response.json()
    assert body["email"] == "owner@veerox-admin.com"
    assert body["org_id"] == str(default_org.id)
    assert body["is_superuser"] is True


async def test_admin_token_login_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A second admin-token login reuses the same auto-provisioned account
    instead of colliding on its unique email."""
    default_org = Org(id=uuid.UUID(settings.default_org_id), name="Veerox Owner Org")
    db_session.add(default_org)
    await db_session.commit()

    first = await client.post("/auth/login", json={"token": settings.admin_token})
    second = await client.post("/auth/login", json={"token": settings.admin_token})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["account_user_id"] == second.json()["account_user_id"]


async def test_login_wrong_token_returns_401(
    client: AsyncClient, account_with_membership: tuple[AccountUser, str]
) -> None:
    response = await client.post("/auth/login", json={"token": "not-the-real-token"})
    assert response.status_code == 401


async def test_me_without_session_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401


async def test_me_with_garbage_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/auth/me", headers={"X-Session-Token": "not-a-real-token"})
    assert response.status_code == 401


async def test_forgot_token_by_email_rotates_token_and_sends_email(
    client: AsyncClient,
    account_with_membership: tuple[AccountUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routers import auth as auth_module

    account, old_token = account_with_membership
    sent: list[tuple[str, str]] = []

    async def _capture_send_email(
        to_email: str, subject: str, html_content: str, to_name: str | None = None
    ) -> dict:
        sent.append((to_email, html_content))
        return {"messageId": "fake"}

    monkeypatch.setattr(auth_module.brevo_client, "send_email", _capture_send_email)

    response = await client.post("/auth/forgot-token", json={"identifier": account.email})
    assert response.status_code == 200
    assert response.json()["message"]
    assert len(sent) == 1
    assert sent[0][0] == account.email

    old_login = await client.post("/auth/login", json={"token": old_token})
    assert old_login.status_code == 401

    new_token = sent[0][1].split("<b>")[1].split("</b>")[0]
    new_login = await client.post("/auth/login", json={"token": new_token})
    assert new_login.status_code == 200


async def test_forgot_token_by_mobile_rotates_token_and_sends_sms(
    client: AsyncClient,
    account_with_mobile: tuple[AccountUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routers import auth as auth_module

    account, old_token = account_with_mobile
    sent: list[tuple[str, str]] = []

    async def _capture_send_sms(to_e164: str, text: str) -> tuple[dict, str]:
        sent.append((to_e164, text))
        return {"message_uuid": "fake"}, "plivo"

    monkeypatch.setattr(auth_module.voice_failover, "send_sms", _capture_send_sms)

    response = await client.post("/auth/forgot-token", json={"identifier": account.mobile})
    assert response.status_code == 200
    assert response.json()["message"]
    assert len(sent) == 1
    assert sent[0][0] == account.mobile

    old_login = await client.post("/auth/login", json={"token": old_token})
    assert old_login.status_code == 401

    new_token = sent[0][1].rsplit(": ", 1)[1]
    new_login = await client.post("/auth/login", json={"token": new_token})
    assert new_login.status_code == 200


async def test_forgot_token_unknown_identifier_returns_generic_message(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.routers import auth as auth_module

    calls = {"email": 0, "sms": 0}

    async def _send_email(
        to_email: str, subject: str, html_content: str, to_name: str | None = None
    ) -> dict:
        calls["email"] += 1
        return {}

    async def _send_sms(to_e164: str, text: str) -> tuple[dict, str]:
        calls["sms"] += 1
        return {}, "plivo"

    monkeypatch.setattr(auth_module.brevo_client, "send_email", _send_email)
    monkeypatch.setattr(auth_module.voice_failover, "send_sms", _send_sms)

    response = await client.post(
        "/auth/forgot-token", json={"identifier": "nobody@example.com"}
    )
    assert response.status_code == 200
    assert response.json()["message"]
    assert calls == {"email": 0, "sms": 0}


async def test_forgot_token_inactive_account_treated_as_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    account_with_membership: tuple[AccountUser, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.routers import auth as auth_module

    account, old_token = account_with_membership
    account.is_active = False
    await db_session.commit()

    calls = {"email": 0}

    async def _send_email(
        to_email: str, subject: str, html_content: str, to_name: str | None = None
    ) -> dict:
        calls["email"] += 1
        return {}

    monkeypatch.setattr(auth_module.brevo_client, "send_email", _send_email)

    response = await client.post("/auth/forgot-token", json={"identifier": account.email})
    assert response.status_code == 200
    assert calls["email"] == 0

    await db_session.refresh(account)
    assert account.token_hash == hash_token(old_token)


async def test_logout_invalidates_session(
    client: AsyncClient, account_with_membership: tuple[AccountUser, str]
) -> None:
    _, token = account_with_membership
    login_response = await client.post("/auth/login", json={"token": token})
    session_token = login_response.json()["token"]

    logout_response = await client.post(
        "/auth/logout", headers={"X-Session-Token": session_token}
    )
    assert logout_response.status_code == 204

    me_response = await client.get("/auth/me", headers={"X-Session-Token": session_token})
    assert me_response.status_code == 401
