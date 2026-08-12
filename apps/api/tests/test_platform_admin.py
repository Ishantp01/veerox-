"""Covers: provisioning an org leaves it without a plan (frontend gates on
this), the platform-admin org being exempt from plan limits, and the
platform-wide org directory only being reachable by a superuser session
or the shared admin token.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership, Plan

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture(autouse=True)
async def _stub_plivo_sms(monkeypatch: pytest.MonkeyPatch) -> None:
    """provision_org SMS's the login token via the real Plivo API — stub it
    so these tests never make a live network call."""
    from apps.api.routers import auth as auth_module

    async def _fake_send_sms(to_e164: str, text: str) -> tuple[dict, str]:
        return {"message_uuid": "fake"}, "plivo"

    monkeypatch.setattr(auth_module.voice_failover, "send_sms", _fake_send_sms)


async def test_provision_org_leaves_org_without_a_plan(client: AsyncClient) -> None:
    provision_response = await client.post(
        "/auth/provision-org",
        json={"org_name": "New Co", "email": "founder2@newco.com", "mobile": "+919876543210"},
        headers=ADMIN_HEADERS,
    )
    assert provision_response.status_code == 201
    org_id = provision_response.json()["org_id"]
    login_token = provision_response.json()["login_token"]

    login = await client.post("/auth/login", json={"token": login_token})
    status_response = await client.get(
        "/billing/status", headers={"X-Session-Token": login.json()["token"]}
    )
    assert status_response.status_code == 200
    assert status_response.json()["plan"] is None
    assert org_id


async def test_superuser_org_exempt_from_plan_limits(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "meta_access_token", None)

    org = Org(id=ORG_ID, name="Platform Org")
    db_session.add(org)
    plan = Plan(
        code="basic",
        name="Basic",
        price_cents_monthly=0,
        limits={"max_whatsapp_messages_per_month": 0},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id

    superuser = AccountUser(
        email="platform-admin@example.com",
        token_hash=hash_token(generate_login_token()),
        is_superuser=True,
    )
    db_session.add(superuser)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=superuser.id, role="admin"))
    await db_session.commit()

    # Limit is 0 (i.e. always over), but the org is superuser-owned, so the
    # send should still succeed rather than 402.
    response = await client.post(
        "/admin/outbound/whatsapp",
        json={"phone": "+910000000095", "text": "hello"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200


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
