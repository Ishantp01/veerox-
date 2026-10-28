"""Authorization of the voice-script library (/admin/scripts).

Scripts are the prompt every live call of an org runs on, so creating, editing, deleting and re-defaulting them is
restricted to org admins (same pattern as the voice-profile settings). Reading stays open to members, organisation
isolation is unchanged, and the shared X-Admin-Token keeps working.
"""

# ruff: noqa: E501, F811  (pytest fixtures are imported by name; long test-name lines)
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership, Script
from apps.api.tests.test_admin_endpoints import ORG_ID, client, fake_redis  # noqa: F401

OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
ADMIN_TOKEN = {"X-Admin-Token": settings.admin_token}
NEW_SCRIPT = {"name": "B", "content": "b", "channel": "voice"}


async def _login(
    client: AsyncClient, db: AsyncSession, *, org_id: uuid.UUID, email: str, role: str
) -> dict[str, str]:
    token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=org_id, account_user_id=account.id, role=role))
    await db.commit()
    login = await client.post("/auth/login", json={"token": token})
    return {"X-Session-Token": login.json()["token"]}


@pytest.fixture
async def seeded(db_session: AsyncSession) -> uuid.UUID:
    """Two orgs; org A owns one default voice script. Returns that script's id."""
    db_session.add(Org(id=ORG_ID, name="Org A"))
    db_session.add(Org(id=OTHER_ORG_ID, name="Org B"))
    script = Script(org_id=ORG_ID, name="Main", content="original", channel="voice", is_default=True)
    db_session.add(script)
    await db_session.commit()
    return script.id


async def _content(db: AsyncSession, script_id: uuid.UUID) -> str | None:
    db.expire_all()
    row = await db.get(Script, script_id)
    return row.content if row else None


# --- admin: every operation works -------------------------------------------------------------------------


async def test_org_admin_can_create_edit_set_default_and_delete(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    admin = await _login(client, db_session, org_id=ORG_ID, email="a@test.com", role="admin")
    created = await client.post("/admin/scripts", json=NEW_SCRIPT, headers=admin)
    assert created.status_code == 201
    new_id = created.json()["id"]
    patched = await client.patch(f"/admin/scripts/{new_id}", json={"content": "b2"}, headers=admin)
    assert patched.status_code == 200
    assert (await client.post(f"/admin/scripts/{new_id}/set-default", headers=admin)).status_code == 200
    assert (await client.delete(f"/admin/scripts/{new_id}", headers=admin)).status_code == 200


async def test_shared_admin_token_is_never_refused(client: AsyncClient, seeded: uuid.UUID) -> None:
    response = await client.post("/admin/scripts", json=NEW_SCRIPT, headers=ADMIN_TOKEN)
    assert response.status_code == 201  # token callers act as the platform org, with no member restriction


# --- member: read yes, change no ---------------------------------------------------------------------------


async def test_member_can_still_list_scripts(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    member = await _login(client, db_session, org_id=ORG_ID, email="m@test.com", role="member")
    response = await client.get("/admin/scripts", headers=member)
    assert response.status_code == 200
    assert [s["id"] for s in response.json()] == [str(seeded)]


async def test_member_cannot_edit_a_script(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    member = await _login(client, db_session, org_id=ORG_ID, email="m1@test.com", role="member")
    response = await client.patch(f"/admin/scripts/{seeded}", json={"content": "hijacked"}, headers=member)
    assert response.status_code == 403
    assert await _content(db_session, seeded) == "original"


async def test_member_cannot_create_a_script(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    member = await _login(client, db_session, org_id=ORG_ID, email="m2@test.com", role="member")
    assert (await client.post("/admin/scripts", json=NEW_SCRIPT, headers=member)).status_code == 403
    assert len((await client.get("/admin/scripts", headers=member)).json()) == 1


async def test_member_cannot_set_default(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    member = await _login(client, db_session, org_id=ORG_ID, email="m3@test.com", role="member")
    assert (await client.post(f"/admin/scripts/{seeded}/set-default", headers=member)).status_code == 403


async def test_member_cannot_delete_a_script(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    member = await _login(client, db_session, org_id=ORG_ID, email="m4@test.com", role="member")
    assert (await client.delete(f"/admin/scripts/{seeded}", headers=member)).status_code == 403
    assert await _content(db_session, seeded) == "original"


async def test_member_is_refused_before_the_script_is_looked_up(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    """A member gets 403 for a missing id too, so the endpoint cannot be used to probe which ids exist."""
    member = await _login(client, db_session, org_id=ORG_ID, email="m5@test.com", role="member")
    assert (await client.delete(f"/admin/scripts/{uuid.uuid4()}", headers=member)).status_code == 403


# --- organisation isolation ----------------------------------------------------------------------------------


async def test_admin_of_another_org_cannot_touch_the_script(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    other = await _login(client, db_session, org_id=OTHER_ORG_ID, email="b@test.com", role="admin")
    patched = await client.patch(f"/admin/scripts/{seeded}", json={"content": "x"}, headers=other)
    assert patched.status_code == 404
    assert (await client.post(f"/admin/scripts/{seeded}/set-default", headers=other)).status_code == 404
    assert (await client.delete(f"/admin/scripts/{seeded}", headers=other)).status_code == 404
    assert await _content(db_session, seeded) == "original"
    assert (await client.get("/admin/scripts", headers=other)).json() == []


async def test_a_created_script_lands_in_the_callers_own_org(
    client: AsyncClient, db_session: AsyncSession, seeded: uuid.UUID
) -> None:
    other = await _login(client, db_session, org_id=OTHER_ORG_ID, email="b2@test.com", role="admin")
    assert (await client.post("/admin/scripts", json=NEW_SCRIPT, headers=other)).status_code == 201
    mine = await _login(client, db_session, org_id=ORG_ID, email="a2@test.com", role="admin")
    names = [s["name"] for s in (await client.get("/admin/scripts", headers=mine)).json()]
    assert names == ["Main"]
