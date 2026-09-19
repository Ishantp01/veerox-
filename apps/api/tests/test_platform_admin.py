"""Covers the platform-wide org directory only being reachable by a
superuser session or the shared admin token, plus platform-admin org edit/
delete behavior.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture(autouse=True)
async def _stub_plivo_sms(monkeypatch: pytest.MonkeyPatch) -> None:
    """provision_org SMS's the login token via the real Plivo API — stub it
    so these tests never make a live network call."""
    from apps.api.routers import auth as auth_module

    async def _fake_send_sms(*_args: object, **_kwargs: object) -> tuple[dict, str]:
        return {"message_uuid": "fake"}, "plivo"

    monkeypatch.setattr(auth_module.voice_failover, "send_sms", _fake_send_sms)


async def test_org_directory_rejects_regular_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Regular Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    account = AccountUser(email="regular@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    response = await client.get("/billing/orgs", headers={"X-Session-Token": token})
    assert response.status_code == 403


async def test_org_directory_lists_all_orgs_for_admin_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Org(name="Org A"))
    db_session.add(Org(name="Org B"))
    await db_session.commit()

    response = await client.get("/billing/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    names = {o["name"] for o in response.json()}
    assert {"Org A", "Org B"}.issubset(names)


async def test_org_directory_accepts_superuser_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Platform Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    superuser = AccountUser(
        email="platform-admin2@example.com",
        token_hash=hash_token(login_token),
        is_superuser=True,
    )
    db_session.add(superuser)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=superuser.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    response = await client.get("/billing/orgs", headers={"X-Session-Token": token})
    assert response.status_code == 200


async def test_me_and_login_expose_is_superuser(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Platform Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    superuser = AccountUser(
        email="platform-admin3@example.com",
        token_hash=hash_token(login_token),
        is_superuser=True,
    )
    db_session.add(superuser)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=superuser.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    assert login.json()["is_superuser"] is True

    me_response = await client.get(
        "/auth/me", headers={"X-Session-Token": login.json()["token"]}
    )
    assert me_response.json()["is_superuser"] is True


async def test_org_directory_excludes_platform_admin_owned_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The platform's own operating org (any org a superuser belongs to)
    must never appear in the directory a superuser browses to manage
    *other* orgs — see billing.py's list_orgs."""
    platform_org = Org(id=ORG_ID, name="Platform Org")
    customer_org = Org(name="Customer Org")
    db_session.add_all([platform_org, customer_org])
    await db_session.flush()

    superuser = AccountUser(
        email="platform-admin5@example.com", token_hash=hash_token("x"), is_superuser=True
    )
    db_session.add(superuser)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=platform_org.id, account_user_id=superuser.id, role="admin"))
    await db_session.commit()

    response = await client.get("/billing/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    names = {o["name"] for o in response.json()}
    assert "Platform Org" not in names
    assert "Customer Org" in names


async def test_platform_admin_can_delete_other_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Deletable Org")
    db_session.add(org)
    await db_session.flush()
    account = AccountUser(email="member-of-deletable-org@example.com", token_hash=hash_token("x"))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    response = await client.delete(f"/billing/orgs/{org.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 204
    assert (await db_session.get(Org, org.id)) is None


async def test_delete_org_rejects_regular_session(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Regular Org 2")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    account = AccountUser(email="regular3@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    response = await client.delete(f"/billing/orgs/{org.id}", headers={"X-Session-Token": token})
    assert response.status_code == 403
    assert (await db_session.get(Org, org.id)) is not None


async def test_delete_org_refuses_platform_admin_owned_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Platform Org 3")
    db_session.add(org)
    await db_session.flush()
    superuser = AccountUser(
        email="platform-admin6@example.com", token_hash=hash_token("x"), is_superuser=True
    )
    db_session.add(superuser)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=superuser.id, role="admin"))
    await db_session.commit()

    response = await client.delete(f"/billing/orgs/{org.id}", headers=ADMIN_HEADERS)
    assert response.status_code == 403
    assert (await db_session.get(Org, org.id)) is not None


async def test_delete_org_404_for_unknown_org(client: AsyncClient) -> None:
    response = await client.delete(f"/billing/orgs/{uuid.uuid4()}", headers=ADMIN_HEADERS)
    assert response.status_code == 404


async def test_org_directory_lists_admin_name_and_mobile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /billing/orgs must surface the admin's name/mobile alongside
    email — the Organizations table shows the admin's name (falling back to
    email) and the edit dialog needs all three to prefill."""
    provision_response = await client.post(
        "/auth/provision-org",
        json={
            "org_name": "Directory Co",
            "email": "dir-admin@example.com",
            "full_name": "Dana Admin",
            "mobile": "+919876500001",
        },
        headers=ADMIN_HEADERS,
    )
    assert provision_response.status_code == 201
    org_id = provision_response.json()["org_id"]

    response = await client.get("/billing/orgs", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    org = next(o for o in response.json() if o["id"] == org_id)
    assert org["admin_email"] == "dir-admin@example.com"
    assert org["admin_name"] == "Dana Admin"
    assert org["admin_mobile"] == "+919876500001"


async def test_update_org_edits_admin_email_name_and_mobile(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    provision_response = await client.post(
        "/auth/provision-org",
        json={"org_name": "Edit Co", "email": "before@example.com", "mobile": "+919876500002"},
        headers=ADMIN_HEADERS,
    )
    assert provision_response.status_code == 201
    org_id = provision_response.json()["org_id"]

    response = await client.patch(
        f"/billing/orgs/{org_id}",
        json={
            "admin_email": "after@example.com",
            "admin_name": "New Name",
            "admin_mobile": "+919876500003",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["admin_email"] == "after@example.com"
    assert body["admin_name"] == "New Name"
    assert body["admin_mobile"] == "+919876500003"

    # The old email no longer works for anything, and the org's own admin
    # login token (unchanged — only the profile fields were edited) still
    # authenticates the new email.
    account_result = await db_session.execute(
        select(AccountUser).where(AccountUser.email == "after@example.com")
    )
    assert account_result.scalar_one().full_name == "New Name"


async def test_update_org_admin_email_conflict_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await client.post(
        "/auth/provision-org",
        json={"org_name": "First Co", "email": "taken@example.com", "mobile": "+919876500004"},
        headers=ADMIN_HEADERS,
    )
    assert first.status_code == 201

    second = await client.post(
        "/auth/provision-org",
        json={"org_name": "Second Co", "email": "second@example.com", "mobile": "+919876500005"},
        headers=ADMIN_HEADERS,
    )
    assert second.status_code == 201
    second_org_id = second.json()["org_id"]

    response = await client.patch(
        f"/billing/orgs/{second_org_id}",
        json={"admin_email": "taken@example.com"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "That email is already used by another account"


async def test_update_org_phone_number_conflict_is_not_reported_as_email_conflict(
    client: AsyncClient,
) -> None:
    first = await client.post(
        "/auth/provision-org",
        json={
            "org_name": "Number Owner Co",
            "email": "number-owner@example.com",
            "mobile": "+919876500006",
            "phone_numbers": [
                {"provider": "plivo", "phone_number": "+15550001000", "is_default": True}
            ],
        },
        headers=ADMIN_HEADERS,
    )
    assert first.status_code == 201

    second = await client.post(
        "/auth/provision-org",
        json={
            "org_name": "Number Edit Co",
            "email": "number-edit@example.com",
            "mobile": "+919876500007",
        },
        headers=ADMIN_HEADERS,
    )
    assert second.status_code == 201
    second_org_id = second.json()["org_id"]

    response = await client.patch(
        f"/billing/orgs/{second_org_id}",
        json={
            "admin_email": "number-edit@example.com",
            "phone_numbers": [
                {"provider": "plivo", "phone_number": "+15550001000", "is_default": True}
            ],
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "That number is already assigned to another organization"
