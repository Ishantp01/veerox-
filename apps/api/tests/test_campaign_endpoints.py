"""Tests for the calling-campaign admin endpoints (apps.api.routers.admin).

Covers create (CSV upload -> staged CampaignTarget rows, not Lead rows),
listing with aggregate counts, detail, and pause/resume. Mirrors the fixture
setup in test_admin_endpoints.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    db_session: AsyncSession, fake_redis: FakeRedis, test_engine, monkeypatch
) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app
    from apps.api.routers import auth as auth_router

    app = create_app()

    # /auth/login (used by the member-scoping tests below) writes via its own
    # AsyncSessionLocal, not the request's db — redirect it at the test engine
    # like conftest's shared client fixture does, else it hits the real DB.
    monkeypatch.setattr(
        auth_router,
        "AsyncSessionLocal",
        async_sessionmaker(bind=test_engine, expire_on_commit=False),
    )

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


@contextmanager
def _require_session_auth(value: bool) -> Iterator[None]:
    original = settings.require_session_auth
    settings.require_session_auth = value
    try:
        yield
    finally:
        settings.require_session_auth = original


async def _login_as(
    client: AsyncClient, db: AsyncSession, *, email: str, role: str
) -> tuple[dict[str, str], uuid.UUID]:
    """Create an org membership with the given role, log in, and return
    ``(X-Session-Token header, account_user_id)``."""
    login_token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    account_id = account.id
    db.add(OrgMembership(org_id=ORG_ID, account_user_id=account_id, role=role))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    return {"X-Session-Token": login.json()["token"]}, account_id


async def _create_campaign_as(
    client: AsyncClient, headers: dict[str, str], *, name: str, phone: str
) -> str:
    with _require_session_auth(True):
        resp = await client.post(
            "/admin/campaigns",
            data={"name": name, "criteria": "n/a", "channel": "voice"},
            files={"file": ("leads.csv", f"name,phone\nA,{phone}\n", "text/csv")},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    return resp.json()["campaign"]["id"]


async def test_list_campaigns_member_only_sees_own(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    a_headers, a_id = await _login_as(client, db_session, email="a@example.com", role="member")
    b_headers, _ = await _login_as(client, db_session, email="b@example.com", role="member")
    admin_headers, _ = await _login_as(client, db_session, email="adm@example.com", role="admin")

    a_campaign = await _create_campaign_as(client, a_headers, name="A camp", phone="+910000000060")
    b_campaign = await _create_campaign_as(client, b_headers, name="B camp", phone="+910000000061")

    with _require_session_auth(True):
        a_list = (await client.get("/admin/campaigns", headers=a_headers)).json()
        b_list = (await client.get("/admin/campaigns", headers=b_headers)).json()
        admin_list = (await client.get("/admin/campaigns", headers=admin_headers)).json()

    assert {c["id"] for c in a_list} == {a_campaign}
    assert {c["id"] for c in b_list} == {b_campaign}
    assert {a_campaign, b_campaign} <= {c["id"] for c in admin_list}

    # Creator attribution persisted + name surfaced for the admin view.
    stored = (
        await db_session.execute(select(CallCampaign).where(CallCampaign.id == uuid.UUID(a_campaign)))
    ).scalar_one()
    assert stored.created_by_account_user_id == a_id
    admin_a_row = next(c for c in admin_list if c["id"] == a_campaign)
    assert admin_a_row["created_by_name"] == "a@example.com"


async def test_campaign_actions_404_for_non_creator_member(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    a_headers, _ = await _login_as(client, db_session, email="a2@example.com", role="member")
    b_headers, _ = await _login_as(client, db_session, email="b2@example.com", role="member")
    cid = await _create_campaign_as(client, a_headers, name="A camp", phone="+910000000062")

    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with _require_session_auth(True):
        assert (await client.get(f"/admin/campaigns/{cid}", headers=b_headers)).status_code == 404
        assert (
            await client.post(f"/admin/campaigns/{cid}/pause", headers=b_headers)
        ).status_code == 404
        assert (
            await client.post(f"/admin/campaigns/{cid}/resume", headers=b_headers)
        ).status_code == 404
        assert (
            await client.post(
                f"/admin/campaigns/{cid}/schedule",
                json={"scheduled_start_at": later},
                headers=b_headers,
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"/admin/campaigns/{cid}", json={"max_attempts": 5}, headers=b_headers
            )
        ).status_code == 404
        # The creator still can.
        assert (await client.get(f"/admin/campaigns/{cid}", headers=a_headers)).status_code == 200


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


async def test_create_campaign_with_whatsapp_script_and_number_ids(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """whatsapp_script_id/whatsapp_number_id are the WhatsApp-only siblings
    of script_id/phone_number_id — same pin-at-creation contract, just
    scoped to a Script row with channel="whatsapp" and an OrgPhoneNumber row
    with provider="whatsapp"."""
    await _seed_org(db_session)
    wa_script = Script(
        org_id=ORG_ID, name="WA Custom", content="Say hi on WhatsApp.", channel="whatsapp", is_default=True
    )
    db_session.add(wa_script)
    await replace_org_phone_numbers(
        db_session, ORG_ID, [OrgPhoneNumberIn(provider="whatsapp", phone_number="109876543210")]
    )
    await db_session.commit()
    wa_number = (await db_session.execute(select(OrgPhoneNumber))).scalar_one()
    csv_body = "name,phone\nAsha,+910000000050\n"

    response = await client.post(
        "/admin/campaigns",
        data={
            "name": "WA pinned campaign",
            "criteria": "n/a",
            "channel": "whatsapp",
            "whatsapp_script_id": str(wa_script.id),
            "whatsapp_number_id": str(wa_number.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()["campaign"]
    assert body["whatsapp_script_id"] == str(wa_script.id)
    assert body["whatsapp_number_id"] == str(wa_number.id)


async def test_create_campaign_rejects_voice_script_as_whatsapp_script(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A voice-channel Script can't be pinned as a campaign's
    whatsapp_script_id — the two libraries are disjoint by channel."""
    await _seed_org(db_session)
    voice_script = Script(org_id=ORG_ID, name="Voice", content="Say hi.", is_default=True)
    db_session.add(voice_script)
    await db_session.commit()
    csv_body = "name,phone\nAsha,+910000000050\n"

    response = await client.post(
        "/admin/campaigns",
        data={
            "name": "Bad campaign",
            "criteria": "n/a",
            "channel": "whatsapp",
            "whatsapp_script_id": str(voice_script.id),
        },
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert "whatsapp_script_id" in response.json()["detail"]


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


async def test_create_campaign_prepends_org_country_code_to_local_numbers(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Plivo dials `to` verbatim, so every stored target must be E.164. A
    number typed without a prefix gets the org's default_country_code; one
    that's unusable even after that is a per-row error."""
    await _seed_org(db_session)
    csv_body = "name,phone\nLocal,9179609988\nIntl,+14155552671\nJunk,12\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "Format check", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert "not a valid phone number" in body["errors"][0]["reason"]

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert sorted(t.phone for t in targets) == ["+14155552671", "+919179609988"]


async def test_create_campaign_uses_the_orgs_own_country_code(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A US org's local numbers get +1, not the +91 default."""
    db_session.add(Org(id=ORG_ID, name="US Org", default_country_code="+1"))
    await db_session.commit()
    csv_body = "name,phone\nLocal,4155552671\nTrunk,04155552672\n"

    response = await client.post(
        "/admin/campaigns",
        data={"name": "US list", "criteria": "n/a", "channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 2
    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert sorted(t.phone for t in targets) == ["+14155552671", "+14155552672"]


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


async def test_sample_campaign_csv_omits_channel_columns_when_locked(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The Voice/WhatsApp Campaigns pages always pass `channel` and are
    locked to that one channel, so the sample template shouldn't include the
    call/whatsapp columns those pages' uploads never use — see
    apps.api.routers.admin's `_sample_columns`."""
    voice_resp = await client.get(
        "/admin/campaigns/sample.csv", params={"channel": "voice"}, headers=ADMIN_HEADERS
    )
    assert voice_resp.status_code == 200
    assert voice_resp.text.lstrip("﻿").splitlines()[0].strip() == "name,phone"

    whatsapp_resp = await client.get(
        "/admin/campaigns/sample.csv", params={"channel": "whatsapp"}, headers=ADMIN_HEADERS
    )
    assert whatsapp_resp.status_code == 200
    assert whatsapp_resp.text.lstrip("﻿").splitlines()[0].strip() == "name,phone"

    unified_resp = await client.get("/admin/campaigns/sample.csv", headers=ADMIN_HEADERS)
    assert unified_resp.status_code == 200
    assert unified_resp.text.lstrip("﻿").splitlines()[0].strip() == "name,phone,call,whatsapp"


async def test_sample_leads_csv_always_includes_channel_columns(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Unlike campaigns, the leads sample template keeps call/whatsapp
    columns regardless of channel — leads import always reads them."""
    resp = await client.get("/admin/leads/sample.csv", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.text.lstrip("﻿").splitlines()[0].strip() == "name,phone,call,whatsapp,status"


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
