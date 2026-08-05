from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership, Plan

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _seed_org(db: AsyncSession, *, plan: Plan | None = None) -> Org:
    org = Org(id=ORG_ID, name="Test Org", plan_id=plan.id if plan else None)
    db.add(org)
    await db.commit()
    return org


@pytest_asyncio.fixture
async def admin_session(client: AsyncClient, db_session: AsyncSession) -> tuple[AccountUser, str]:
    org = await _seed_org(db_session)
    token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login_response = await client.post("/auth/login", json={"token": token})
    session_token = login_response.json()["token"]
    return account, session_token


def _auth(session_token: str) -> dict[str, str]:
    return {"X-Session-Token": session_token}


async def test_admin_can_invite_new_member(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    response = await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "teammate@example.com"
    assert body["role"] == "member"
    assert body["login_token"]


async def test_invite_rejects_duplicate_member(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    response = await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    assert response.status_code == 409


async def test_member_cannot_invite(client: AsyncClient, db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    token = generate_login_token()
    account = AccountUser(email="member@example.com", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="member"))
    await db_session.commit()

    login_response = await client.post("/auth/login", json={"token": token})
    session_token = login_response.json()["token"]

    response = await client.post(
        "/team/members",
        json={"email": "another@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    assert response.status_code == 403


async def test_list_members_returns_org_scoped_team(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    response = await client.get("/team/members", headers=_auth(session_token))
    assert response.status_code == 200
    emails = {member["email"] for member in response.json()}
    assert emails == {"admin@example.com", "teammate@example.com"}


async def test_admin_can_change_member_role(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    invite_response = await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    account_user_id = invite_response.json()["account_user_id"]

    response = await client.patch(
        f"/team/members/{account_user_id}",
        json={"role": "admin"},
        headers=_auth(session_token),
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_cannot_demote_last_admin(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    account, session_token = admin_session
    response = await client.patch(
        f"/team/members/{account.id}",
        json={"role": "member"},
        headers=_auth(session_token),
    )
    assert response.status_code == 400


async def test_admin_can_remove_member(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    invite_response = await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    account_user_id = invite_response.json()["account_user_id"]

    response = await client.delete(
        f"/team/members/{account_user_id}", headers=_auth(session_token)
    )
    assert response.status_code == 204

    list_response = await client.get("/team/members", headers=_auth(session_token))
    emails = {member["email"] for member in list_response.json()}
    assert "teammate@example.com" not in emails


async def test_cannot_remove_last_admin(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    account, session_token = admin_session
    response = await client.delete(f"/team/members/{account.id}", headers=_auth(session_token))
    assert response.status_code == 400


async def test_admin_can_regenerate_member_token(
    client: AsyncClient, admin_session: tuple[AccountUser, str], db_session: AsyncSession
) -> None:
    _, session_token = admin_session
    invite_response = await client.post(
        "/team/members",
        json={"email": "teammate@example.com", "role": "member"},
        headers=_auth(session_token),
    )
    account_user_id = invite_response.json()["account_user_id"]
    old_token = invite_response.json()["login_token"]

    response = await client.post(
        f"/team/members/{account_user_id}/regenerate-token", headers=_auth(session_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "teammate@example.com"
    new_token = body["login_token"]
    assert new_token != old_token

    old_login = await client.post("/auth/login", json={"token": old_token})
    assert old_login.status_code == 401

    new_login = await client.post("/auth/login", json={"token": new_token})
    assert new_login.status_code == 200


async def test_regenerate_token_404_for_non_member(
    client: AsyncClient, admin_session: tuple[AccountUser, str]
) -> None:
    _, session_token = admin_session
    response = await client.post(
        f"/team/members/{uuid.uuid4()}/regenerate-token", headers=_auth(session_token)
    )
    assert response.status_code == 404


async def test_member_cannot_regenerate_token(client: AsyncClient, db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    admin_token = generate_login_token()
    admin = AccountUser(email="admin2@example.com", token_hash=hash_token(admin_token))
    db_session.add(admin)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=admin.id, role="admin"))

    member_token = generate_login_token()
    member = AccountUser(email="member@example.com", token_hash=hash_token(member_token))
    db_session.add(member)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=member.id, role="member"))
    await db_session.commit()

    login_response = await client.post("/auth/login", json={"token": member_token})
    session_token = login_response.json()["token"]

    response = await client.post(
        f"/team/members/{admin.id}/regenerate-token", headers=_auth(session_token)
    )
    assert response.status_code == 403
