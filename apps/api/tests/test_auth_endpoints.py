from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


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


async def test_provision_org_creates_org_user_and_membership(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/provision-org",
        json={"org_name": "Acme", "email": "founder@acme.com"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "founder@acme.com"
    assert body["login_token"]


async def test_provision_org_requires_admin(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/provision-org", json={"org_name": "Acme", "email": "founder@acme.com"}
    )
    assert response.status_code == 403


async def test_provision_org_rejects_duplicate_email(
    client: AsyncClient, account_with_membership: tuple[AccountUser, str]
) -> None:
    response = await client.post(
        "/auth/provision-org",
        json={"org_name": "Another Org", "email": "admin@example.com"},
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
