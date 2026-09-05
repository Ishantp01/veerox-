"""Tests for the calling-campaign admin endpoints (apps.api.routers.admin).

Covers create (CSV upload -> staged CampaignTarget rows, not Lead rows),
listing with aggregate counts, detail, and pause/resume. Mirrors the fixture
setup in test_admin_endpoints.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice.org_numbers import replace_org_phone_numbers
from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import (
    AccountUser,
    CallCampaign,
    CampaignTarget,
    Lead,
    Org,
    OrgMembership,
    Plan,
    Script,
)
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.deps import get_db, get_redis_dep
from apps.api.schemas.org_numbers import OrgPhoneNumberIn
from apps.api.tests.conftest import FakeRedis

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest_asyncio.fixture
async def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
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


async def test_create_campaign_stages_targets_not_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nAsha,+910000000050\nRavi,+910000000051\n"

    response = await client.post(
        "/admin/campaigns",
        data={
            "name": "July outreach",
            "criteria": "Wants a demo and has budget",
            "channel": "voice",
            "start_mode": "now",
        },
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


async def test_create_campaign_with_script_and_phone_number_ids(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    script = Script(org_id=ORG_ID, name="Custom", content="Say hi.", is_default=True)
    db_session.add(script)
    await replace_org_phone_numbers(
        db_session, ORG_ID, [OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001")]
    )
    await db_session.commit()
    number = (await db_session.execute(select(OrgPhoneNumber))).scalar_one()
    csv_body = "name,phone\nAsha,+910000000050\n"

    response = await client.post(
        "/admin/campaigns",
        data={
            "name": "Pinned campaign",
            "criteria": "n/a",
            "channel": "voice",
            "script_id": str(script.id),
            "phone_number_id": str(number.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()["campaign"]
    assert body["script_id"] == str(script.id)
    assert body["phone_number_id"] == str(number.id)


async def test_create_campaign_persists_max_attempts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000051\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Two tries", "criteria": "n/a", "channel": "voice", "max_attempts": "2"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["campaign"]["max_attempts"] == 2


async def test_create_campaign_defaults_max_attempts_to_three(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000052\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Default tries", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["campaign"]["max_attempts"] == 3


async def test_create_campaign_allows_large_max_attempts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """No upper bound — an org that wants to keep retrying can set any
    positive integer."""
    await _seed_org(db_session)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000053\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Persistent", "criteria": "n/a", "channel": "voice", "max_attempts": "25"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["campaign"]["max_attempts"] == 25


async def test_create_campaign_rejects_zero_max_attempts(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000054\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Never call", "criteria": "n/a", "channel": "voice", "max_attempts": "0"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert "max_attempts" in response.json()["detail"]


async def test_create_campaign_rejects_script_from_another_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    other_org_id = uuid.uuid4()
    db_session.add(Org(id=other_org_id, name="Other Org"))
    other_script = Script(org_id=other_org_id, name="Not mine", content="x", is_default=True)
    db_session.add(other_script)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000050\n"

    response = await client.post(
        "/admin/campaigns",
        data={
            "name": "Should fail",
            "criteria": "n/a",
            "channel": "voice",
            "script_id": str(other_script.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert "script_id" in response.json()["detail"]


async def test_create_campaign_rejects_phone_without_country_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Plivo dials `to` verbatim — a bare 10-digit number never rings, so
    campaign uploads must be validated the same way the Dial page is."""
    await _seed_org(db_session)
    csv_body = "name,phone\nBad Number,9179609988\nGood Number,+919179609988\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Format check", "criteria": "n/a", "channel": "voice"},
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


async def test_create_campaign_dedupes_repeated_phone_in_upload(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The same number listed twice must not become two targets — otherwise
    that contact would be dialed 2x the campaign's `max_attempts`."""
    await _seed_org(db_session)
    csv_body = (
        "name,phone\n"
        "Asha,+910000000060\n"
        "Asha again,+910000000060\n"
        "Ravi,+910000000061\n"
    )

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Dup list", "criteria": "n/a", "channel": "voice", "start_mode": "now"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert "duplicate" in body["errors"][0]["reason"]

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert sorted(t.phone for t in targets) == ["+910000000060", "+910000000061"]


async def test_create_campaign_reports_missing_phone_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nNo Phone,\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Bad list", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert body["errors"][0]["reason"] == "missing phone"


async def test_create_campaign_limit_resets_after_plan_renewal(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(id=ORG_ID, name="Test Org")
    db_session.add(org)
    plan = Plan(
        code="starter",
        name="Starter",
        price_cents=0,
        limits={"max_campaigns": 1},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id
    org.plan_started_at = datetime.now(UTC)
    login_token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role="admin"))
    db_session.add(
        CallCampaign(
            org_id=ORG_ID,
            name="Before renewal",
            criteria="n/a",
            channel="voice",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db_session.commit()
    login = await client.post("/auth/login", json={"token": login_token})
    headers = {"X-Session-Token": login.json()["token"]}

    response = await client.post(
        "/admin/campaigns",
        data={"name": "After renewal", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", "name,phone\nA,+910000000054\n", "text/csv")},
        headers=headers,
    )
    assert response.status_code == 200

    blocked = await client.post(
        "/admin/campaigns",
        data={"name": "Second current campaign", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", "name,phone\nB,+910000000055\n", "text/csv")},
        headers=headers,
    )
    assert blocked.status_code == 402


async def test_list_and_get_campaign(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    create_resp = await client.post(
        "/admin/campaigns",
        data={"name": "Q3 leads", "criteria": "Must want a callback", "channel": "voice"},
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
        data={"name": "Pausable", "criteria": "n/a", "channel": "voice"},
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


async def test_update_campaign_repoints_pinned_script(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A campaign's script_id is otherwise fixed forever at creation — this
    is the escape hatch for pointing an existing campaign at a newly
    edited/added script in the library instead (the "old script keeps
    playing" bug: editing the library alone never reaches a campaign that
    already pinned a specific script_id)."""
    await _seed_org(db_session)
    old_script = Script(org_id=ORG_ID, name="Old", content="old content", is_default=False)
    new_script = Script(org_id=ORG_ID, name="New", content="new content", is_default=True)
    db_session.add_all([old_script, new_script])
    await db_session.commit()
    csv_body = "name,phone\nA,+910000000060\n"
    create_resp = await client.post(
        "/admin/campaigns",
        data={
            "name": "Repin me",
            "criteria": "n/a",
            "channel": "voice",
            "script_id": str(old_script.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )
    campaign_id = create_resp.json()["campaign"]["id"]
    assert create_resp.json()["campaign"]["script_id"] == str(old_script.id)

    update_resp = await client.patch(
        f"/admin/campaigns/{campaign_id}",
        json={"script_id": str(new_script.id)},
        headers=ADMIN_HEADERS,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["script_id"] == str(new_script.id)

    campaign = await db_session.get(CallCampaign, uuid.UUID(campaign_id))
    await db_session.refresh(campaign)
    assert campaign.script_id == new_script.id


async def test_update_campaign_clears_script_to_org_default(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    script = Script(org_id=ORG_ID, name="Pinned", content="x", is_default=True)
    db_session.add(script)
    await db_session.commit()
    csv_body = "name,phone\nA,+910000000061\n"
    create_resp = await client.post(
        "/admin/campaigns",
        data={
            "name": "Clear me",
            "criteria": "n/a",
            "channel": "voice",
            "script_id": str(script.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )
    campaign_id = create_resp.json()["campaign"]["id"]

    update_resp = await client.patch(
        f"/admin/campaigns/{campaign_id}",
        json={"script_id": None},
        headers=ADMIN_HEADERS,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["script_id"] is None


async def test_update_campaign_rejects_script_from_another_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    other_org_id = uuid.uuid4()
    db_session.add(Org(id=other_org_id, name="Other Org"))
    other_script = Script(org_id=other_org_id, name="Not mine", content="x", is_default=True)
    db_session.add(other_script)
    csv_body = "name,phone\nA,+910000000062\n"
    create_resp = await client.post(
        "/admin/campaigns",
        data={"name": "Guarded", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )
    campaign_id = create_resp.json()["campaign"]["id"]

    update_resp = await client.patch(
        f"/admin/campaigns/{campaign_id}",
        json={"script_id": str(other_script.id)},
        headers=ADMIN_HEADERS,
    )
    assert update_resp.status_code == 400
    assert "script_id" in update_resp.json()["detail"]


async def test_update_campaign_404_for_unknown_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.patch(
        f"/admin/campaigns/{uuid.uuid4()}",
        json={"max_attempts": 5},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404
