"""Tests for the qualification-criteria preset library admin endpoints
(apps.api.routers.admin's /admin/qualification-criteria-presets CRUD).

Mirrors the fixture setup in test_script_endpoints.py.
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


async def test_list_presets_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)

    response = await client.get("/admin/qualification-criteria-presets", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == []


async def test_create_preset(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)

    response = await client.post(
        "/admin/qualification-criteria-presets",
        json={"name": "Demo interest", "criteria_text": "Prospect must confirm interest in a demo."},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Demo interest"
    assert body["criteria_text"] == "Prospect must confirm interest in a demo."

    listed = (await client.get("/admin/qualification-criteria-presets", headers=ADMIN_HEADERS)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


async def test_update_preset_renames_and_edits_text(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    created = (
        await client.post(
            "/admin/qualification-criteria-presets",
            json={"name": "Old name", "criteria_text": "Old criteria"},
            headers=ADMIN_HEADERS,
        )
    ).json()

    response = await client.patch(
        f"/admin/qualification-criteria-presets/{created['id']}",
        json={"name": "New name", "criteria_text": "New criteria"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New name"
    assert body["criteria_text"] == "New criteria"


async def test_delete_preset(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    created = (
        await client.post(
            "/admin/qualification-criteria-presets",
            json={"name": "First", "criteria_text": "A"},
            headers=ADMIN_HEADERS,
        )
    ).json()

    response = await client.delete(
        f"/admin/qualification-criteria-presets/{created['id']}", headers=ADMIN_HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    listed = (await client.get("/admin/qualification-criteria-presets", headers=ADMIN_HEADERS)).json()
    assert listed == []


async def test_update_and_delete_404_for_unknown_preset(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    missing_id = uuid.uuid4()

    patch_response = await client.patch(
        f"/admin/qualification-criteria-presets/{missing_id}", json={"name": "x"}, headers=ADMIN_HEADERS
    )
    delete_response = await client.delete(
        f"/admin/qualification-criteria-presets/{missing_id}", headers=ADMIN_HEADERS
    )

    assert patch_response.status_code == 404
    assert delete_response.status_code == 404
