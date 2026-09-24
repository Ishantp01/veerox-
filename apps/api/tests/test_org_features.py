"""Covers the platform-admin-managed per-org feature checklist
(Org.enabled_features, enforced by deps.py's require_feature) and team
member cap (Org.max_team_members, enforced by routers/team.py's
invite_member).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from collections.abc import Iterator

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@contextmanager
def _require_session_auth(value: bool) -> Iterator[None]:
    original = settings.require_session_auth
    settings.require_session_auth = value
    try:
        yield
    finally:
        settings.require_session_auth = original


async def _login_as(client: AsyncClient, db: AsyncSession, *, org_id: uuid.UUID, email: str, role: str) -> dict[str, str]:
    login_token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=org_id, account_user_id=account.id, role=role))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]
    return {"X-Session-Token": token}


async def test_disabled_feature_blocks_org_scoped_request(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Org(id=ORG_ID, name="Feature-Gated Co"))
    await db_session.commit()
    headers = await _login_as(client, db_session, org_id=ORG_ID, email="rep@gated.example", role="admin")

    with _require_session_auth(True):
        ok_response = await client.get("/crm/contacts", headers=headers)
    assert ok_response.status_code == 200

    patch_resp = await client.patch(
        f"/billing/orgs/{ORG_ID}", json={"enabled_features": []}, headers=ADMIN_HEADERS
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled_features"] == []

    with _require_session_auth(True):
        blocked_response = await client.get("/crm/contacts", headers=headers)
    assert blocked_response.status_code == 403
    assert blocked_response.json()["detail"]["error"] == "feature_disabled"
    assert blocked_response.json()["detail"]["feature"] == "crm"


async def test_human_support_and_follow_up_tasks_can_be_switched_off_per_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Org(id=ORG_ID, name="Feature-Gated Co 2"))
    await db_session.commit()
    headers = await _login_as(client, db_session, org_id=ORG_ID, email="rep@gated2.example", role="admin")

    with _require_session_auth(True):
        assert (await client.get("/admin/human-support", headers=headers)).status_code == 200
        assert (await client.get("/lead-follow-ups", headers=headers)).status_code == 200

    # Only Human Support ticked -> follow-up tasks blocked, human support allowed.
    patch = await client.patch(
        f"/billing/orgs/{ORG_ID}", json={"enabled_features": ["human_support"]}, headers=ADMIN_HEADERS
    )
    assert patch.status_code == 200
    with _require_session_auth(True):
        assert (await client.get("/admin/human-support", headers=headers)).status_code == 200
        blocked = await client.get("/lead-follow-ups", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["feature"] == "follow_up_tasks"

    # Nothing ticked -> Human Support blocked too.
    await client.patch(f"/billing/orgs/{ORG_ID}", json={"enabled_features": []}, headers=ADMIN_HEADERS)
    with _require_session_auth(True):
        blocked = await client.get("/admin/human-support", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["feature"] == "human_support"


async def test_update_org_rejects_unknown_feature_key(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Unknown Feature Co"))
    await db_session.commit()

    response = await client.patch(
        f"/billing/orgs/{ORG_ID}", json={"enabled_features": ["not_a_real_feature"]}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 422


async def test_platform_org_is_exempt_from_feature_restriction(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.deps import DEFAULT_ORG_ID

    # Pre-seed the platform's own operating org with every feature
    # explicitly disabled, then have the admin-token login flow attach its
    # owner account to it (see routers/auth.py's _ensure_default_org_owner)
    # — require_feature must still let it through via
    # _org_is_platform_admin_owned, regardless of enabled_features.
    db_session.add(Org(id=DEFAULT_ORG_ID, name="Platform Org", enabled_features=[]))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": settings.admin_token})
    assert login.status_code == 200
    session_token = login.json()["token"]

    with _require_session_auth(True):
        response = await client.get("/crm/contacts", headers={"X-Session-Token": session_token})
    assert response.status_code == 200


async def test_invite_member_respects_team_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    org = Org(name="Capped Co", max_team_members=1)
    db_session.add(org)
    await db_session.flush()
    admin_headers = await _login_as(client, db_session, org_id=org.id, email="owner@capped.example", role="admin")

    # The org owner's own OrgMembership row doesn't count against the cap —
    # the first invited member should succeed.
    first = await client.post(
        "/team/members",
        json={"email": "second@capped.example", "role": "member"},
        headers=admin_headers,
    )
    assert first.status_code == 201

    response = await client.post(
        "/team/members",
        json={"email": "third@capped.example", "role": "member"},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Team member limit reached for this organization"
