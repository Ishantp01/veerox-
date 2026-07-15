"""Tests for the channel-scoping added to apps.api.routers.admin.

Covers the new ``channel`` query filters on /admin/conversations,
/admin/leads, /admin/escalations, plus the new whatsapp/per-channel fields
on /admin/stats. Redis is monkeypatched with an in-process fake so these
tests are hermetic (no live Redis needed).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import CallCampaign, CampaignTarget, Conversation, Lead, Message, Org, User
from apps.api.deps import get_db, get_redis_dep

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


class _FakeRedis:
    """Minimal Redis stand-in for /admin/stats' error counter and
    /admin/escalations' human_handoff_queue LRANGE."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])


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


async def test_list_conversations_filters_by_channel(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000001")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Conversation(org_id=ORG_ID, user_id=user.id, channel="voice"))
    db_session.add(Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp"))
    await db_session.commit()

    response = await client.get(
        "/admin/conversations", params={"channel": "voice"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["channel"] == "voice"


async def test_list_leads_filters_by_channel(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000002")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="whatsapp"))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="voice"))
    await db_session.commit()

    response = await client.get(
        "/admin/leads", params={"channel": "whatsapp"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["channel"] == "whatsapp"


async def test_list_leads_filters_by_intent_substring(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Intent is a freeform LLM-captured sentence, not a fixed category — the
    filter must be a case-insensitive substring match, not exact equality."""
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000009")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, intent="Book an appointment on July 8th")
    )
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, intent="Interested in purchasing software")
    )
    await db_session.commit()

    response = await client.get(
        "/admin/leads", params={"intent": "APPOINTMENT"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["intent"] == "Book an appointment on July 8th"


async def test_list_leads_filters_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000040")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", status="new"))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", status="contacted"))
    await db_session.commit()

    response = await client.get(
        "/admin/leads", params={"status": "contacted"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["status"] == "contacted"


async def test_new_lead_defaults_to_status_new(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000041")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote"))
    await db_session.commit()

    response = await client.get("/admin/leads", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["status"] == "new"
    assert rows[0]["follow_up_at"] is None
    assert rows[0]["follow_up_note"] is None


async def test_get_lead_returns_conversation_history(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000042")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="whatsapp")
    db_session.add(lead)
    conv = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp")
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        Message(
            org_id=ORG_ID,
            conversation_id=conv.id,
            user_id=user.id,
            role="user",
            content="hi",
            channel="whatsapp",
        )
    )
    await db_session.commit()

    response = await client.get(f"/admin/leads/{lead.id}", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(lead.id)
    assert len(body["conversations"]) == 1
    assert body["conversations"][0]["id"] == str(conv.id)
    assert body["conversations"][0]["message_count"] == 1


async def test_get_lead_404_for_unknown_id(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    response = await client.get(f"/admin/leads/{uuid.uuid4()}", headers=ADMIN_HEADERS)
    assert response.status_code == 404


async def test_update_lead_status_only_touches_sent_fields(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000043")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(
        org_id=ORG_ID,
        user_id=user.id,
        intent="quote",
        follow_up_note="call back Monday",
    )
    db_session.add(lead)
    await db_session.commit()

    response = await client.patch(
        f"/admin/leads/{lead.id}",
        json={"status": "contacted"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "contacted"
    assert body["follow_up_note"] == "call back Monday"  # untouched


async def test_update_lead_can_set_and_clear_follow_up(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000044")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="quote")
    db_session.add(lead)
    await db_session.commit()

    set_response = await client.patch(
        f"/admin/leads/{lead.id}",
        json={"follow_up_at": "2026-08-01T10:00:00Z", "follow_up_note": "callback"},
        headers=ADMIN_HEADERS,
    )
    assert set_response.status_code == 200
    assert set_response.json()["follow_up_note"] == "callback"

    clear_response = await client.patch(
        f"/admin/leads/{lead.id}",
        json={"follow_up_at": None, "follow_up_note": None},
        headers=ADMIN_HEADERS,
    )
    assert clear_response.status_code == 200
    body = clear_response.json()
    assert body["follow_up_at"] is None
    assert body["follow_up_note"] is None


async def test_update_lead_rejects_invalid_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000045")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="quote")
    db_session.add(lead)
    await db_session.commit()

    response = await client.patch(
        f"/admin/leads/{lead.id}",
        json={"status": "bogus"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422


async def test_update_lead_404_for_unknown_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.patch(
        f"/admin/leads/{uuid.uuid4()}",
        json={"status": "contacted"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404


async def test_leads_csv_includes_channel_column(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000003")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="voice"))
    await db_session.commit()

    response = await client.get("/admin/leads.csv", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert lines[0] == "id,name,phone,intent,channel,status,created_at"
    assert lines[1].split(",")[3:6] == ["quote", "voice", "new"]


async def test_leads_csv_filters_by_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000005")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="a", status="qualified"))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="b", status="new"))
    await db_session.commit()

    response = await client.get(
        "/admin/leads.csv", params={"status": "qualified"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 2  # header + one qualified row
    assert lines[1].split(",")[5] == "qualified"


async def test_import_leads_csv_stages_campaign_not_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Bulk-imported rows are staged as a CallCampaign + CampaignTarget rows,
    not written straight to the CRM — only qualify_lead (fired by the AI
    during voice/WhatsApp outreach) promotes a target into a Lead."""
    await _seed_org(db_session)
    csv_body = "name,phone,intent\nAsha,+910000000010,Book a demo\nRavi,+910000000011,\n"

    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["campaign"]["channel"] == "voice"
    assert body["campaign"]["status"] == "running"

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert {t.phone for t in targets} == {"+910000000010", "+910000000011"}
    assert all(t.status == "pending" for t in targets)
    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert leads == []


async def test_import_leads_csv_accepts_custom_campaign_name_and_channel(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nAsha,+910000000012\n"

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "whatsapp"},
        data={"campaign_name": "Inbound signup backlog", "criteria": "Confirms budget"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["campaign"]["name"] == "Inbound signup backlog"
    assert body["campaign"]["criteria"] == "Confirms budget"
    assert body["campaign"]["channel"] == "whatsapp"

    campaign = (await db_session.execute(select(CallCampaign))).scalar_one()
    assert campaign.channel == "whatsapp"


async def test_import_leads_csv_reports_row_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone\nNo Phone,\nBad Format,9179609989\n"

    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 2
    reasons = {e["reason"] for e in body["errors"]}
    assert "missing phone" in reasons
    assert any("country code" in r for r in reasons)


async def test_import_leads_csv_rejects_missing_phone_column(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", "name,intent\nAsha,demo\n", "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400


async def test_import_leads_rejects_unsupported_file_type(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.txt", "name,phone\nAsha,+910000000013\n", "text/plain")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400


def _make_xlsx_bytes(rows: list[list[str]]) -> bytes:
    """Build an in-memory .xlsx workbook (header row + data rows) for import tests."""
    import io as _io

    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    for row in rows:
        sheet.append(row)
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_import_leads_xlsx_stages_campaign_not_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    xlsx_bytes = _make_xlsx_bytes(
        [
            ["name", "phone", "intent"],
            ["Asha", "+910000000020", "Book a demo"],
            ["Ravi", "+910000000021", ""],
        ]
    )

    response = await client.post(
        "/admin/leads/import",
        files={
            "file": (
                "leads.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert {t.phone for t in targets} == {"+910000000020", "+910000000021"}
    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert leads == []


async def test_import_leads_xlsx_rejects_missing_phone_column(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    xlsx_bytes = _make_xlsx_bytes([["name", "intent"], ["Asha", "demo"]])

    response = await client.post(
        "/admin/leads/import",
        files={
            "file": (
                "leads.xlsx",
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400


async def test_import_leads_bulk_json_stages_campaign_not_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)

    response = await client.post(
        "/admin/leads/bulk",
        json={
            "leads": [
                {"name": "Asha", "phone": "+910000000030", "intent": "Book a demo"},
                {"name": "Ravi", "phone": "+910000000031"},
            ],
            "channel": "whatsapp",
            "campaign_name": "API import",
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["campaign"]["name"] == "API import"
    assert body["campaign"]["channel"] == "whatsapp"

    campaign = (await db_session.execute(select(CallCampaign))).scalar_one()
    assert campaign.channel == "whatsapp"
    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert {t.phone for t in targets} == {"+910000000030", "+910000000031"}
    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert leads == []


async def test_import_leads_bulk_json_defaults_channel_to_voice(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)

    response = await client.post(
        "/admin/leads/bulk",
        json={"leads": [{"name": "Asha", "phone": "+910000000033"}]},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["campaign"]["channel"] == "voice"


async def test_import_leads_bulk_json_reports_row_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)

    response = await client.post(
        "/admin/leads/bulk",
        json={"leads": [{"name": "Bad Format", "phone": "9179609990"}]},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1
    assert "country code" in body["errors"][0]["reason"]


async def test_stats_includes_whatsapp_and_per_channel_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000004")
    db_session.add(user)
    await db_session.flush()
    conv = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp")
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        Message(
            org_id=ORG_ID,
            conversation_id=conv.id,
            user_id=user.id,
            role="user",
            content="hi",
            channel="whatsapp",
        )
    )
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="whatsapp"))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", channel="voice"))
    await db_session.commit()

    response = await client.get("/admin/stats", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["whatsapp_messages_today"] == 1
    assert body["leads_today_whatsapp"] == 1
    assert body["leads_today_voice"] == 1


async def test_escalations_filters_queue_entries_by_channel(
    client: AsyncClient, db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000005")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, intent="escalation", channel="voice")
    )
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, intent="escalation", channel="whatsapp")
    )
    await db_session.commit()

    fake_redis.lists["human_handoff_queue"] = [
        json.dumps({"reason": "a", "urgency": "low", "channel": "voice"}),
        json.dumps({"reason": "b", "urgency": "high", "channel": "whatsapp"}),
    ]

    response = await client.get(
        "/admin/escalations", params={"channel": "voice"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["recent_leads"]) == 1
    assert body["recent_leads"][0]["channel"] == "voice"
    assert len(body["queue"]) == 1
    assert body["queue"][0]["channel"] == "voice"


async def test_whatsapp_settings_reports_unconfigured_when_creds_unset(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "meta_access_token", None)
    monkeypatch.setattr(config_module.settings, "meta_phone_number_id", None)
    monkeypatch.setattr(config_module.settings, "meta_app_id", None)
    monkeypatch.setattr(config_module.settings, "meta_app_secret", None)
    monkeypatch.setattr(config_module.settings, "meta_verify_token", None)

    response = await client.get("/admin/settings/whatsapp", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["access_token_configured"] is False
    assert body["app_id_configured"] is False
    assert body["webhook_url"].endswith("/webhook/whatsapp")


async def test_whatsapp_settings_reports_configured_when_creds_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "meta_access_token", "token-123")
    monkeypatch.setattr(config_module.settings, "meta_phone_number_id", "1555000")

    response = await client.get("/admin/settings/whatsapp", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["access_token_configured"] is True
    assert body["phone_number_id"] == "1555000"


async def test_calling_settings_reports_unconfigured_when_creds_unset(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "plivo_auth_id", None)
    monkeypatch.setattr(config_module.settings, "plivo_auth_token", None)
    monkeypatch.setattr(config_module.settings, "plivo_phone_number", None)

    response = await client.get("/admin/settings/calling", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["auth_id_configured"] is False
    assert body["answer_webhook_url"].endswith("/voice/answer")


async def test_calling_settings_reports_configured_when_creds_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "plivo_auth_id", "auth-id")
    monkeypatch.setattr(config_module.settings, "plivo_auth_token", "auth-token")
    monkeypatch.setattr(config_module.settings, "plivo_phone_number", "+15550001111")

    response = await client.get("/admin/settings/calling", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["phone_number"] == "+15550001111"
