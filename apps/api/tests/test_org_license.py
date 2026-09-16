"""Covers the manually admin-managed org license: the issue/renew/extend/
suspend/reactivate endpoints (all duration-in-days, not calendar dates),
enforce_org_license blocking a suspended/expired org's requests, and the
background worker auto-expiring a license whose expires_at has passed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def member_session(db_session: AsyncSession) -> tuple[Org, str]:
    org = Org(id=ORG_ID, name="Licensed Co")
    db_session.add(org)
    await db_session.flush()
    token = generate_login_token()
    account = AccountUser(email="member@licensedco.example", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()
    return org, token


async def test_issue_license_sets_active_status_and_expiry(client: AsyncClient) -> None:
    org_resp = await client.post(
        "/auth/provision-org",
        json={"org_name": "Fresh Co", "email": "founder@freshco.example", "mobile": "+919876543210"},
        headers=ADMIN_HEADERS,
    )
    assert org_resp.status_code == 201
    org_id = org_resp.json()["org_id"]

    response = await client.post(
        f"/billing/orgs/{org_id}/license/issue",
        json={"days": 30, "notes": "paid via bank transfer"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["license_status"] == "active"
    assert body["license_expires_at"] is not None
    assert body["license_duration_days"] == 30
    assert body["license_notes"] == "paid via bank transfer"


async def test_suspend_then_reactivate_round_trips(client: AsyncClient) -> None:
    org_resp = await client.post(
        "/auth/provision-org",
        json={"org_name": "Roundtrip Co", "email": "owner@roundtrip.example", "mobile": "+919876500011"},
        headers=ADMIN_HEADERS,
    )
    org_id = org_resp.json()["org_id"]

    suspend_resp = await client.post(
        f"/billing/orgs/{org_id}/license/suspend",
        json={"notes": "payment dispute"},
        headers=ADMIN_HEADERS,
    )
    assert suspend_resp.status_code == 200
    assert suspend_resp.json()["license_status"] == "suspended"

    # No expiry was ever set (fresh org) — reactivating with no `days` must
    # still succeed rather than demanding one, since there's no "already
    # passed" expiry to worry about.
    reactivate_resp = await client.post(
        f"/billing/orgs/{org_id}/license/reactivate", json={}, headers=ADMIN_HEADERS
    )
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["license_status"] == "active"


async def test_reactivate_with_past_expiry_falls_back_to_default_duration(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No `days` given and no Org.license_duration_days on record (this org
    predates it) — reactivate still succeeds, using
    DEFAULT_LICENSE_DURATION_DAYS rather than requiring an explicit value."""
    org = Org(
        name="Lapsed Co",
        license_status="suspended",
        license_expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(org)
    await db_session.commit()
    org_id = str(org.id)

    no_days_resp = await client.post(
        f"/billing/orgs/{org_id}/license/reactivate", json={}, headers=ADMIN_HEADERS
    )
    assert no_days_resp.status_code == 200
    body = no_days_resp.json()
    assert body["license_status"] == "active"
    assert datetime.fromisoformat(body["license_expires_at"]) > datetime.now(UTC)

    with_days_resp = await client.post(
        f"/billing/orgs/{org_id}/license/reactivate",
        json={"days": 7},
        headers=ADMIN_HEADERS,
    )
    assert with_days_resp.status_code == 200
    assert with_days_resp.json()["license_duration_days"] == 7


async def test_renew_without_days_reuses_last_duration(client: AsyncClient) -> None:
    org_resp = await client.post(
        "/auth/provision-org",
        json={"org_name": "Reuse Co", "email": "owner@reuse.example", "mobile": "+919876500014"},
        headers=ADMIN_HEADERS,
    )
    org_id = org_resp.json()["org_id"]

    issue_resp = await client.post(
        f"/billing/orgs/{org_id}/license/issue", json={"days": 90}, headers=ADMIN_HEADERS
    )
    assert issue_resp.json()["license_duration_days"] == 90

    quick_renew_resp = await client.post(
        f"/billing/orgs/{org_id}/license/renew", json={}, headers=ADMIN_HEADERS
    )
    assert quick_renew_resp.status_code == 200
    body = quick_renew_resp.json()
    assert body["license_duration_days"] == 90
    expiry = datetime.fromisoformat(body["license_expires_at"])
    assert expiry > datetime.now(UTC) + timedelta(days=89)


async def test_extend_license_adds_days_to_expiry(client: AsyncClient) -> None:
    org_resp = await client.post(
        "/auth/provision-org",
        json={"org_name": "Extend Co", "email": "owner@extend.example", "mobile": "+919876500013"},
        headers=ADMIN_HEADERS,
    )
    org_id = org_resp.json()["org_id"]

    renew_resp = await client.post(
        f"/billing/orgs/{org_id}/license/renew", json={"days": 5}, headers=ADMIN_HEADERS
    )
    base = datetime.fromisoformat(renew_resp.json()["license_expires_at"])
    response = await client.post(
        f"/billing/orgs/{org_id}/license/extend", json={"days": 10}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 200
    new_expiry = datetime.fromisoformat(response.json()["license_expires_at"])
    assert new_expiry > base + timedelta(days=9)


async def test_suspended_org_member_blocked_from_org_scoped_request(
    client: AsyncClient, member_session: tuple[Org, str]
) -> None:
    org, token = member_session
    login = await client.post("/auth/login", json={"token": token})
    assert login.status_code == 200
    session_token = login.json()["token"]

    # Sanity: an active license (the default) doesn't block.
    ok_response = await client.get("/team/members", headers={"X-Session-Token": session_token})
    assert ok_response.status_code == 200

    suspend_resp = await client.post(
        f"/billing/orgs/{org.id}/license/suspend", json={}, headers=ADMIN_HEADERS
    )
    assert suspend_resp.status_code == 200

    blocked_response = await client.get("/team/members", headers={"X-Session-Token": session_token})
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"]["error"] == "license_inactive"
    assert blocked_response.json()["detail"]["status"] == "suspended"


async def test_expired_org_member_blocked_from_org_scoped_request(
    client: AsyncClient, db_session: AsyncSession, member_session: tuple[Org, str]
) -> None:
    org, token = member_session
    login = await client.post("/auth/login", json={"token": token})
    session_token = login.json()["token"]

    # Simulate what workers/license_expiry_worker.py does once an expiry has
    # passed — set directly rather than going through the API, since the
    # renew/reactivate endpoints only ever compute a future expiry.
    org.license_status = "expired"
    org.license_expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    blocked_response = await client.get("/team/members", headers={"X-Session-Token": session_token})
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"]["status"] == "expired"


async def test_platform_org_is_exempt_from_license_enforcement(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"token": settings.admin_token})
    assert login.status_code == 200
    session_token = login.json()["token"]

    response = await client.get("/team/members", headers={"X-Session-Token": session_token})
    assert response.status_code == 200


async def test_license_expiry_worker_expires_due_orgs(
    db_session: AsyncSession, test_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api.workers import license_expiry_worker

    # The worker opens its own AsyncSessionLocal() (background-task pattern,
    # not a request-scoped dependency) — redirect it at the same test
    # sqlite engine `db_session` uses, same as conftest.py's `client`
    # fixture does for other background-session modules.
    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(license_expiry_worker, "AsyncSessionLocal", test_session_factory)

    due_org = Org(
        name="Due Org",
        license_status="active",
        license_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    future_org = Org(
        name="Future Org",
        license_status="active",
        license_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add_all([due_org, future_org])
    await db_session.commit()

    await license_expiry_worker._expire_due_licenses()

    await db_session.refresh(due_org)
    await db_session.refresh(future_org)
    assert due_org.license_status == "expired"
    assert future_org.license_status == "active"
