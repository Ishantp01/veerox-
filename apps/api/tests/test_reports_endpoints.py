"""Tests for the reports/analytics admin endpoints (apps.api.routers.admin).

Covers GET /admin/reports/timeseries (daily trend buckets, portable across
Postgres/SQLite via func.date) and GET /admin/reports/campaigns (per-campaign
qualification-rate table).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import (
    CallCampaign,
    CampaignTarget,
    Conversation,
    Lead,
    Message,
    Org,
    User,
)
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


async def test_reports_timeseries_buckets_by_day_across_channels(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000060")
    db_session.add(user)
    await db_session.flush()

    today = datetime.now(UTC)
    yesterday = today - timedelta(days=1)

    db_session.add(Conversation(org_id=ORG_ID, user_id=user.id, channel="voice", started_at=today))
    db_session.add(
        Conversation(org_id=ORG_ID, user_id=user.id, channel="voice", started_at=yesterday)
    )
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, channel="voice", status="qualified", created_at=today))
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, channel="whatsapp", status="qualified", created_at=today)
    )
    await db_session.commit()

    response = await client.get(
        "/admin/reports/timeseries", params={"days": 7}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    points = {p["date"]: p for p in response.json()}
    today_key = today.date().isoformat()
    yesterday_key = yesterday.date().isoformat()

    assert points[today_key]["calls"] == 1
    assert points[today_key]["leads_voice"] == 1
    assert points[today_key]["leads_whatsapp"] == 1
    assert points[today_key]["qualified_count"] == 2
    assert points[yesterday_key]["calls"] == 1
    assert points[yesterday_key]["leads_voice"] == 0


async def test_reports_timeseries_respects_days_window(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000061")
    db_session.add(user)
    await db_session.flush()

    long_ago = datetime.now(UTC) - timedelta(days=90)
    db_session.add(
        Conversation(org_id=ORG_ID, user_id=user.id, channel="voice", started_at=long_ago)
    )
    await db_session.commit()

    response = await client.get(
        "/admin/reports/timeseries", params={"days": 7}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    dates = {p["date"] for p in response.json()}
    assert long_ago.date().isoformat() not in dates


async def test_reports_campaigns_computes_qualification_rate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    campaign = CallCampaign(org_id=ORG_ID, name="Q3 outreach", criteria="n/a", channel="voice")
    db_session.add(campaign)
    await db_session.flush()

    db_session.add(
        CampaignTarget(
            campaign_id=campaign.id, org_id=ORG_ID, phone="+910000000070",
            status="completed", qualified=True,
        )
    )
    db_session.add(
        CampaignTarget(
            campaign_id=campaign.id, org_id=ORG_ID, phone="+910000000071",
            status="completed", qualified=False,
        )
    )
    db_session.add(
        CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone="+910000000072", status="pending")
    )
    await db_session.commit()

    response = await client.get("/admin/reports/campaigns", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Q3 outreach"
    assert row["channel"] == "voice"
    assert row["counts"] == {"pending": 1, "calling": 0, "completed": 2, "failed": 0, "qualified": 1}
    assert row["qualification_rate"] == 0.5


async def test_reports_campaigns_null_rate_when_nothing_resolved(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    campaign = CallCampaign(org_id=ORG_ID, name="Fresh campaign", criteria="n/a", channel="whatsapp")
    db_session.add(campaign)
    await db_session.flush()
    db_session.add(
        CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone="+910000000073", status="pending")
    )
    await db_session.commit()

    response = await client.get("/admin/reports/campaigns", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    row = response.json()[0]
    assert row["qualification_rate"] is None
