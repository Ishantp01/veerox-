"""Tests for apps.api.routers.crm's Contact endpoints.

Contact visibility is siloed by creator (created_by_account_user_id) for
EVERY role, admin included — unlike Lead, there is no org-wide visibility
exception (see db/models/contact.py's docstring). Uses the global
client/db_session fixtures from conftest.py, which already redirect
/auth/login's background write to the test engine and provide a full
pipeline-capable FakeRedis — see test_router_auth_guard.py for the same
session-login pattern.
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

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@contextmanager
def _require_session_auth(value: bool) -> Iterator[None]:
    original = settings.require_session_auth
    settings.require_session_auth = value
    try:
        yield
    finally:
        settings.require_session_auth = original


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def _login_as(client: AsyncClient, db: AsyncSession, *, email: str, role: str) -> dict[str, str]:
    login_token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role=role))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]
    return {"X-Session-Token": token}


async def test_create_contact_records_creator(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    headers = await _login_as(client, db_session, email="rep1@example.com", role="member")

    with _require_session_auth(True):
        response = await client.post(
            "/crm/contacts", json={"phone": "+919876543210", "name": "Asha"}, headers=headers
        )
    assert response.status_code == 201
    body = response.json()
    assert body["created_by_account_user_id"] is not None


async def test_create_contact_rejects_duplicate_within_own_list(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    headers = await _login_as(client, db_session, email="rep1@example.com", role="member")

    with _require_session_auth(True):
        first = await client.post("/crm/contacts", json={"phone": "+919876543299"}, headers=headers)
        assert first.status_code == 201
        second = await client.post("/crm/contacts", json={"phone": "+919876543299"}, headers=headers)
    assert second.status_code == 409


async def test_create_contact_allows_same_phone_for_different_members(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        first = await client.post(
            "/crm/contacts", json={"phone": "+919876543298"}, headers=rep1_headers
        )
        second = await client.post(
            "/crm/contacts", json={"phone": "+919876543298"}, headers=rep2_headers
        )
    assert first.status_code == 201
    assert second.status_code == 201


async def test_list_contacts_only_shows_own(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        await client.post(
            "/crm/contacts", json={"phone": "+919876543211", "name": "Rep1's contact"}, headers=rep1_headers
        )
        await client.post(
            "/crm/contacts", json={"phone": "+919876543212", "name": "Rep2's contact"}, headers=rep2_headers
        )

        rep1_list = await client.get("/crm/contacts", headers=rep1_headers)
        rep2_list = await client.get("/crm/contacts", headers=rep2_headers)

    assert [c["name"] for c in rep1_list.json()] == ["Rep1's contact"]
    assert [c["name"] for c in rep2_list.json()] == ["Rep2's contact"]


async def test_admin_does_not_see_member_contacts(client: AsyncClient, db_session: AsyncSession) -> None:
    """Confirms the explicit design choice: admin is siloed too, no org-wide
    override for contacts (unlike leads)."""
    await _seed_org(db_session)
    admin_headers = await _login_as(client, db_session, email="admin@example.com", role="admin")
    member_headers = await _login_as(client, db_session, email="member@example.com", role="member")

    with _require_session_auth(True):
        await client.post(
            "/crm/contacts", json={"phone": "+919876543213", "name": "Member's contact"}, headers=member_headers
        )
        admin_list = await client.get("/crm/contacts", headers=admin_headers)

    assert admin_list.json() == []


async def test_get_contact_404_for_non_creator(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        create_response = await client.post(
            "/crm/contacts", json={"phone": "+919876543214"}, headers=rep1_headers
        )
        contact_id = create_response.json()["id"]

        own_get = await client.get(f"/crm/contacts/{contact_id}", headers=rep1_headers)
        other_get = await client.get(f"/crm/contacts/{contact_id}", headers=rep2_headers)

    assert own_get.status_code == 200
    assert other_get.status_code == 404


async def test_update_contact_404_for_non_creator(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        create_response = await client.post(
            "/crm/contacts", json={"phone": "+919876543215"}, headers=rep1_headers
        )
        contact_id = create_response.json()["id"]

        response = await client.patch(
            f"/crm/contacts/{contact_id}", json={"name": "Hijacked"}, headers=rep2_headers
        )

    assert response.status_code == 404


async def test_delete_contact_404_for_non_creator(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        create_response = await client.post(
            "/crm/contacts", json={"phone": "+919876543216"}, headers=rep1_headers
        )
        contact_id = create_response.json()["id"]

        response = await client.delete(f"/crm/contacts/{contact_id}", headers=rep2_headers)

    assert response.status_code == 404


async def test_import_updates_only_importers_own_contact(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")

    with _require_session_auth(True):
        await client.post(
            "/crm/contacts", json={"phone": "+919876543217", "name": "Original"}, headers=rep1_headers
        )
        csv_content = b"name,phone,email,company\r\nUpdated Name,+919876543217,,\r\n"
        response = await client.post(
            "/crm/contacts/import",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            headers=rep1_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    assert body["imported"] == 0
    assert body["errors"] == []


async def test_import_adds_own_copy_of_a_phone_another_member_already_has(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Each team member's contact list is independent — a phone number
    someone else in the org already has doesn't block adding your own
    contact for it; it's treated exactly like a phone nobody has yet."""
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")
    rep2_headers = await _login_as(client, db_session, email="rep2@example.com", role="member")

    with _require_session_auth(True):
        await client.post(
            "/crm/contacts", json={"phone": "+919876543218", "name": "Rep1's"}, headers=rep1_headers
        )
        csv_content = b"name,phone,email,company\r\nRep2's own copy,+919876543218,,\r\n"
        response = await client.post(
            "/crm/contacts/import",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            headers=rep2_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["updated"] == 0
    assert body["skipped"] == 0
    assert body["errors"] == []

    # Both reps now have their own independent contact for the same number.
    with _require_session_auth(True):
        rep1_list = await client.get("/crm/contacts", headers=rep1_headers)
        rep2_list = await client.get("/crm/contacts", headers=rep2_headers)
    assert rep1_list.json()[0]["name"] == "Rep1's"
    assert rep2_list.json()[0]["name"] == "Rep2's own copy"


async def test_import_updates_only_when_reimporting_own_phone_not_someone_elses(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The "only skipped if it's already in the importer's OWN list" rule:
    re-importing a phone you already have updates your row; the same phone
    imported by someone else creates a separate, independent row for them
    (asserted above) rather than being skipped or stolen."""
    await _seed_org(db_session)
    rep1_headers = await _login_as(client, db_session, email="rep1@example.com", role="member")

    with _require_session_auth(True):
        await client.post(
            "/crm/contacts", json={"phone": "+919876543219", "name": "Old Name"}, headers=rep1_headers
        )
        csv_content = b"name,phone,email,company\r\nNew Name,+919876543219,,\r\n"
        response = await client.post(
            "/crm/contacts/import",
            files={"file": ("contacts.csv", csv_content, "text/csv")},
            headers=rep1_headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["updated"] == 1

    with _require_session_auth(True):
        rep1_list = await client.get("/crm/contacts", headers=rep1_headers)
    assert len(rep1_list.json()) == 1
    assert rep1_list.json()[0]["name"] == "New Name"
