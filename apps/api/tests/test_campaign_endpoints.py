"""Tests for the calling-campaign admin endpoints (apps.api.routers.admin).

Covers create (CSV upload -> staged CampaignTarget rows, not Lead rows),
listing with aggregate counts, detail, and pause/resume. Mirrors the fixture
setup in test_admin_endpoints.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import CampaignTarget, Lead, Org
from apps.api.deps import get_db, get_redis_dep

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


@pytest_asyncio.fixture
async def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[_FakeRedis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def test_create_campaign_stages_targets_not_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nAsha,+910000000050\nRavi,+910000000051\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "July outreach", "criteria": "Wants a demo and has budget"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["campaign"]["name"] == "July outreach"
    assert body["campaign"]["status"] == "running"
    assert body["campaign"]["counts"] == {
        "pending": 2,
        "calling": 0,
        "completed": 0,
        "failed": 0,
        "qualified": 0,
    }

    # Uploaded contacts are staged, not written straight into the CRM leads table.
    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert len(targets) == 2
    assert all(t.status == "pending" for t in targets)
    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert leads == []


async def test_create_campaign_rejects_phone_without_country_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Plivo dials `to` verbatim — a bare 10-digit number never rings, so
    campaign uploads must be validated the same way the Dial page is."""
    await _seed_org(db_session)
    csv_body = "name,phone\nBad Number,9179609988\nGood Number,+919179609988\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Format check", "criteria": "n/a"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped"] == 1
    assert "country code" in body["errors"][0]["reason"]

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert len(targets) == 1
    assert targets[0].phone == "+919179609988"


async def test_create_campaign_reports_missing_phone_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nNo Phone,\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Bad list", "criteria": "n/a"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert body["errors"][0]["reason"] == "missing phone"


async def test_list_and_get_campaign(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    create_resp = await client.post(
        "/admin/campaigns",
        data={"name": "Q3 leads", "criteria": "Must want a callback"},
        files={"file": ("leads.csv", "name,phone\nA,+910000000052\n", "text/csv")},
        headers=ADMIN_HEADERS,
    )
    campaign_id = create_resp.json()["campaign"]["id"]

    list_resp = await client.get("/admin/campaigns", headers=ADMIN_HEADERS)
    assert list_resp.status_code == 200
    assert any(c["id"] == campaign_id for c in list_resp.json())

    detail_resp = await client.get(f"/admin/campaigns/{campaign_id}", headers=ADMIN_HEADERS)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == campaign_id
    assert len(detail["targets"]) == 1
    assert detail["targets"][0]["phone"] == "+910000000052"


async def test_get_campaign_404_for_unknown_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.get(f"/admin/campaigns/{uuid.uuid4()}", headers=ADMIN_HEADERS)
    assert response.status_code == 404


async def test_pause_and_resume_campaign(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    create_resp = await client.post(
        "/admin/campaigns",
        data={"name": "Pausable", "criteria": "n/a"},
        files={"file": ("leads.csv", "name,phone\nA,+910000000053\n", "text/csv")},
        headers=ADMIN_HEADERS,
    )
    campaign_id = create_resp.json()["campaign"]["id"]

    pause_resp = await client.post(
        f"/admin/campaigns/{campaign_id}/pause", headers=ADMIN_HEADERS
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    resume_resp = await client.post(
        f"/admin/campaigns/{campaign_id}/resume", headers=ADMIN_HEADERS
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "running"
