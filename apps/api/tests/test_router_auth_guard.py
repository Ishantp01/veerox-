"""Covers the Phase 2/3 rollout: admin.py's ~30 endpoints and the
previously-open routers (leads, appointments, conversations, crm,
follow-ups, sales, templates) now share one router-level guard
(verify_admin_or_session). Admin-token access must be unchanged; the new
session-token path only activates when settings.require_session_auth is on.

Each session-auth test sets the flag explicitly and restores the original
value afterward rather than assuming a particular default, since that
default is a deployment decision independent of this test's intent.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@contextmanager
def _require_session_auth(value: bool) -> Iterator[None]:
    original = settings.require_session_auth
    settings.require_session_auth = value
    try:
        yield
    finally:
        settings.require_session_auth = original


async def test_leads_router_now_requires_admin_token(client: AsyncClient) -> None:
    response = await client.get("/leads")
    assert response.status_code == 403


async def test_leads_router_accepts_admin_token(client: AsyncClient) -> None:
    response = await client.get("/leads", headers=ADMIN_HEADERS)
    assert response.status_code == 200


async def test_admin_stats_still_requires_admin_token(client: AsyncClient) -> None:
    response = await client.get("/admin/stats")
    assert response.status_code == 403


async def test_admin_stats_accepts_admin_token(client: AsyncClient) -> None:
    response = await client.get("/admin/stats", headers=ADMIN_HEADERS)
    assert response.status_code == 200


async def test_session_token_rejected_when_flag_off(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Test Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    with _require_session_auth(False):
        response = await client.get("/leads", headers={"X-Session-Token": token})
    assert response.status_code == 403


async def test_session_token_accepted_when_flag_on(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Test Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    account = AccountUser(email="admin2@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    with _require_session_auth(True):
        response = await client.get("/leads", headers={"X-Session-Token": token})
    assert response.status_code == 200


async def test_session_token_from_removed_member_rejected_when_flag_on(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Test Org")
    db_session.add(org)
    await db_session.flush()
    login_token = generate_login_token()
    account = AccountUser(email="admin3@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    membership = OrgMembership(org_id=org.id, account_user_id=account.id, role="admin")
    db_session.add(membership)
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    await db_session.delete(membership)
    await db_session.commit()

    with _require_session_auth(True):
        response = await client.get("/leads", headers={"X-Session-Token": token})
    assert response.status_code == 403
