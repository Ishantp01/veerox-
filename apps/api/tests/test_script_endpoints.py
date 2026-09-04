"""Tests for the voice-calling script library admin endpoints
(apps.api.routers.admin's /admin/scripts CRUD).

Covers: an org's first script is always forced default, creating a second
default unsets the first, set-default/delete/update behave as documented.
Mirrors the fixture setup in test_campaign_endpoints.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Org
from apps.api.deps import get_db, get_redis_dep
from apps.api.tests.conftest import FakeRedis

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest_asyncio.fixture
async def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, fake_redis: FakeRedis) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def test_list_scripts_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)

    response = await client.get("/admin/scripts", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == []


async def test_create_first_script_forces_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An org is never left with zero default scripts once it has one —
    the very first script becomes default even if is_default wasn't set."""
    await _seed_org(db_session)

    response = await client.post(
        "/admin/scripts",
        json={"name": "Default", "content": "Say hello.", "is_default": False},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["is_default"] is True
    assert body["name"] == "Default"


async def test_create_second_script_not_default_by_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await client.post(
        "/admin/scripts", json={"name": "First", "content": "A"}, headers=ADMIN_HEADERS
    )

    response = await client.post(
        "/admin/scripts", json={"name": "Second", "content": "B"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 201
    assert response.json()["is_default"] is False

    listed = (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json()
    defaults = [s for s in listed if s["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "First"


async def test_creating_new_default_unsets_previous(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await client.post(
        "/admin/scripts", json={"name": "First", "content": "A"}, headers=ADMIN_HEADERS
    )

    response = await client.post(
        "/admin/scripts",
        json={"name": "Second", "content": "B", "is_default": True},
        headers=ADMIN_HEADERS,
    )
    assert response.json()["is_default"] is True

    listed = (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json()
    defaults = [s for s in listed if s["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "Second"


async def test_set_default_switches_default_atomically(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    first = (
        await client.post(
            "/admin/scripts", json={"name": "First", "content": "A"}, headers=ADMIN_HEADERS
        )
    ).json()
    second = (
        await client.post(
            "/admin/scripts", json={"name": "Second", "content": "B"}, headers=ADMIN_HEADERS
        )
    ).json()
    assert first["is_default"] is True
    assert second["is_default"] is False

    response = await client.post(f"/admin/scripts/{second['id']}/set-default", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["is_default"] is True

    listed = (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json()
    by_id = {s["id"]: s for s in listed}
    assert by_id[first["id"]]["is_default"] is False
    assert by_id[second["id"]]["is_default"] is True


async def test_update_script_renames_and_edits_content(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    created = (
        await client.post(
            "/admin/scripts", json={"name": "Old name", "content": "Old content"}, headers=ADMIN_HEADERS
        )
    ).json()

    response = await client.patch(
        f"/admin/scripts/{created['id']}",
        json={"name": "New name", "content": "New content"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New name"
    assert body["content"] == "New content"
    assert body["is_default"] is True  # untouched by a content-only update


async def test_delete_script_does_not_promote_another_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    first = (
        await client.post(
            "/admin/scripts", json={"name": "First", "content": "A"}, headers=ADMIN_HEADERS
        )
    ).json()
    await client.post("/admin/scripts", json={"name": "Second", "content": "B"}, headers=ADMIN_HEADERS)

    response = await client.delete(f"/admin/scripts/{first['id']}", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    listed = (await client.get("/admin/scripts", headers=ADMIN_HEADERS)).json()
    assert len(listed) == 1
    assert not any(s["is_default"] for s in listed)


async def test_update_and_delete_404_for_unknown_script(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    missing_id = uuid.uuid4()

    patch_response = await client.patch(
        f"/admin/scripts/{missing_id}", json={"name": "x"}, headers=ADMIN_HEADERS
    )
    delete_response = await client.delete(f"/admin/scripts/{missing_id}", headers=ADMIN_HEADERS)

    assert patch_response.status_code == 404
    assert delete_response.status_code == 404
