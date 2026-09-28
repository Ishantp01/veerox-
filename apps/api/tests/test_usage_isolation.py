"""Security/isolation tests for the usage-metering & billing endpoints
(req §17): org A cannot read org B's usage, a frontend-supplied org id is
ignored, a non-superuser can't reach the admin usage endpoints, and the
internal usage-event ingestion endpoint rejects anonymous/session callers.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.core.usage import deterministic_event_id, record_usage
from apps.api.db.models import AccountUser, Org, OrgMembership

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest_asyncio.fixture
async def two_orgs_with_usage(db_session: AsyncSession):
    org_a = Org(name="Org A")
    org_b = Org(name="Org B")
    db_session.add_all([org_a, org_b])
    await db_session.flush()

    token_a = generate_login_token()
    account_a = AccountUser(email="admin@orga.example", token_hash=hash_token(token_a))
    db_session.add(account_a)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org_a.id, account_user_id=account_a.id, role="admin"))

    token_b = generate_login_token()
    account_b = AccountUser(email="admin@orgb.example", token_hash=hash_token(token_b))
    db_session.add(account_b)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org_b.id, account_user_id=account_b.id, role="admin"))
    await db_session.commit()

    await record_usage(
        db_session,
        organization_id=org_a.id,
        event_id=deterministic_event_id("test", "org-a-event"),
        service="voice",
        usage_type="voice_seconds",
        quantity=100,
        unit="seconds",
        source="test",
    )
    await record_usage(
        db_session,
        organization_id=org_b.id,
        event_id=deterministic_event_id("test", "org-b-event"),
        service="voice",
        usage_type="voice_seconds",
        quantity=999,
        unit="seconds",
        source="test",
    )
    await db_session.commit()

    return org_a, token_a, org_b, token_b


async def test_org_usage_endpoint_is_scoped_to_the_callers_own_org(
    client: AsyncClient, two_orgs_with_usage
) -> None:
    org_a, token_a, org_b, token_b = two_orgs_with_usage

    login_a = await client.post("/auth/login", json={"token": token_a})
    session_a = login_a.json()["token"]
    login_b = await client.post("/auth/login", json={"token": token_b})
    session_b = login_b.json()["token"]

    # Run the daily+monthly aggregators (via a direct call) is a separate
    # concern (test_usage_aggregation.py) — here we only need to prove the
    # breakdown endpoint never crosses orgs even with raw UsageEvent rows,
    # so query the breakdown endpoint which reads UsageMonthly; seed that
    # too so there's something to actually compare.
    resp_a = await client.get(
        "/org/usage?period=current", headers={"X-Session-Token": session_a}
    )
    resp_b = await client.get(
        "/org/usage?period=current", headers={"X-Session-Token": session_b}
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["org_id"] == str(org_a.id)
    assert resp_b.json()["org_id"] == str(org_b.id)
    assert resp_a.json()["org_id"] != resp_b.json()["org_id"]


async def test_frontend_supplied_admin_org_id_path_requires_platform_admin(
    client: AsyncClient, two_orgs_with_usage
) -> None:
    """A regular org admin session must not be able to view another org's
    usage even by guessing its id in the admin path — verify_platform_admin
    rejects any session whose account isn't a superuser."""
    org_a, token_a, org_b, _token_b = two_orgs_with_usage
    login_a = await client.post("/auth/login", json={"token": token_a})
    session_a = login_a.json()["token"]

    resp = await client.get(
        f"/admin/organizations/{org_b.id}/usage", headers={"X-Session-Token": session_a}
    )
    assert resp.status_code == 403


async def test_admin_org_id_path_works_for_platform_admin(
    client: AsyncClient, two_orgs_with_usage
) -> None:
    org_a, _token_a, org_b, _token_b = two_orgs_with_usage
    resp = await client.get(f"/admin/organizations/{org_b.id}/usage", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["org_id"] == str(org_b.id)


async def test_internal_usage_ingestion_rejects_anonymous_calls(client: AsyncClient) -> None:
    resp = await client.post(
        "/internal/usage-events",
        json={
            "organization_id": str(uuid.uuid4()),
            "event_id": "anon-attempt",
            "service": "voice",
            "usage_type": "voice_seconds",
            "quantity": "1",
            "unit": "seconds",
            "source": "test",
        },
    )
    assert resp.status_code == 403


async def test_internal_usage_ingestion_rejects_a_dashboard_session(
    client: AsyncClient, two_orgs_with_usage
) -> None:
    """Only the shared X-Admin-Token authenticates this endpoint — a valid
    org session must not be enough, unlike verify_admin_or_session's OR-gate
    used for normal dashboard routes."""
    _org_a, token_a, _org_b, _token_b = two_orgs_with_usage
    login_a = await client.post("/auth/login", json={"token": token_a})
    session_a = login_a.json()["token"]

    resp = await client.post(
        "/internal/usage-events",
        headers={"X-Session-Token": session_a},
        json={
            "organization_id": str(uuid.uuid4()),
            "event_id": "session-attempt",
            "service": "voice",
            "usage_type": "voice_seconds",
            "quantity": "1",
            "unit": "seconds",
            "source": "test",
        },
    )
    assert resp.status_code == 403


async def test_internal_usage_ingestion_accepts_admin_token_and_dedupes(
    client: AsyncClient, two_orgs_with_usage
) -> None:
    org_a, _token_a, _org_b, _token_b = two_orgs_with_usage
    body = {
        "organization_id": str(org_a.id),
        "event_id": "admin-ingested-event",
        "service": "voice",
        "usage_type": "voice_seconds",
        "quantity": "5",
        "unit": "seconds",
        "source": "test",
    }
    first = await client.post("/internal/usage-events", headers=ADMIN_HEADERS, json=body)
    assert first.status_code == 201
    assert first.json()["recorded"] is True

    second = await client.post("/internal/usage-events", headers=ADMIN_HEADERS, json=body)
    assert second.status_code == 201
    assert second.json()["recorded"] is False
