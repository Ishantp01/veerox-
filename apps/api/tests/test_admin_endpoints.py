"""Tests for the channel-scoping added to apps.api.routers.admin.

Covers the new ``channel`` query filters on /admin/conversations,
/admin/leads, /admin/escalations, plus the new whatsapp/per-channel fields
on /admin/stats. Redis is monkeypatched with an in-process fake so these
tests are hermetic (no live Redis needed).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import (
    AccountUser,
    CallCampaign,
    CampaignTarget,
    Conversation,
    Lead,
    Message,
    Org,
    OrgMembership,
    OrgPhoneNumber,
    User,
)
from apps.api.deps import get_db, get_redis_dep

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


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
) -> dict[str, str]:
    """Create an org membership with the given role, log in, and return a
    ready-to-use `X-Session-Token` header for that account."""
    login_token = generate_login_token()
    account = AccountUser(email=email, token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role=role))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]
    return {"X-Session-Token": token}


class _FakePipeline:
    """Enough of redis-py's pipeline (as an async context manager) for
    core/sessions.py's create_session — queues SET/SADD, replays on execute()."""

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._queue: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _queue_call(*args, **kwargs):
            self._queue.append((name, args, kwargs))
            return self

        return _queue_call

    async def execute(self) -> list:
        results = []
        for name, args, kwargs in self._queue:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queue.clear()
        return results

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeRedis:
    """Minimal Redis stand-in for /admin/stats' error counter,
    /admin/escalations' human_handoff_queue LRANGE, and session
    login/logout (SET/SADD/SREM/DELETE via a pipeline)."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.kv.pop(key, None)
            self.lists.pop(key, None)
            self.sets.pop(key, None)

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values: str) -> None:
        self.sets.get(key, set()).difference_update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])

    def pipeline(self, transaction: bool = True) -> "_FakePipeline":
        return _FakePipeline(self)


@pytest_asyncio.fixture
async def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    test_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from apps.api.main import create_app
    from apps.api.routers import auth as auth_router

    app = create_app()

    # /auth/login's background _record_last_login opens its own
    # AsyncSessionLocal() rather than reusing the request's `db` — see
    # conftest.py's own `client` fixture for the same redirect and why it's
    # needed (otherwise it hits the real configured database instead of this
    # test's in-memory engine).
    monkeypatch.setattr(
        auth_router, "AsyncSessionLocal", async_sessionmaker(bind=test_engine, expire_on_commit=False)
    )

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


async def test_list_leads_filters_by_tag(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000010")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", tags=["hot", "enterprise"]))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", tags=["cold"]))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", tags=None))
    await db_session.commit()

    response = await client.get("/admin/leads", params={"tag": "hot"}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["tags"] == ["hot", "enterprise"]


async def test_update_lead_sets_tags(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000011")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="quote")
    db_session.add(lead)
    await db_session.commit()

    response = await client.patch(
        f"/admin/leads/{lead.id}",
        json={"tags": ["hot", "needs-demo"]},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["tags"] == ["hot", "needs-demo"]


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


async def test_list_leads_status_qualified_also_matches_qualification_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The leads-view filter only offers one "Qualified" option (not two
    near-duplicate ones — see apps/web/src/components/leads/leads-view.tsx),
    so status=qualified must match either field: the pipeline stage
    (Lead.status) or the separate rep-review workflow
    (Lead.qualification_status).
    """
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000042")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Lead(org_id=ORG_ID, user_id=user.id, intent="quote", status="qualified")
    )
    db_session.add(
        Lead(
            org_id=ORG_ID,
            user_id=user.id,
            intent="quote",
            status="contacted",
            qualification_status="qualified",
        )
    )
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="quote", status="new"))
    await db_session.commit()

    response = await client.get(
        "/admin/leads", params={"status": "qualified"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"qualified", "contacted"}


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


async def test_list_leads_member_only_sees_assigned_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000020")
    db_session.add(user)
    await db_session.flush()

    member_headers = await _login_as(
        client, db_session, email="member@example.com", role="member"
    )
    member_id = (
        await db_session.execute(select(AccountUser).where(AccountUser.email == "member@example.com"))
    ).scalar_one().id

    assigned = Lead(org_id=ORG_ID, user_id=user.id, intent="assigned", claimed_by_account_user_id=member_id)
    unassigned = Lead(org_id=ORG_ID, user_id=user.id, intent="unassigned")
    db_session.add_all([assigned, unassigned])
    await db_session.commit()

    with _require_session_auth(True):
        response = await client.get("/admin/leads", headers=member_headers)
    assert response.status_code == 200
    body = response.json()
    assert [lead["id"] for lead in body] == [str(assigned.id)]


async def test_list_leads_admin_sees_all_org_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000021")
    db_session.add(user)
    await db_session.flush()

    admin_headers = await _login_as(client, db_session, email="admin@example.com", role="admin")

    db_session.add_all(
        [
            Lead(org_id=ORG_ID, user_id=user.id, intent="a"),
            Lead(org_id=ORG_ID, user_id=user.id, intent="b"),
        ]
    )
    await db_session.commit()

    with _require_session_auth(True):
        response = await client.get("/admin/leads", headers=admin_headers)
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_lead_404_for_member_lead_not_assigned_to_them(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000022")
    db_session.add(user)
    await db_session.flush()

    member_headers = await _login_as(
        client, db_session, email="member2@example.com", role="member"
    )
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="unassigned")
    db_session.add(lead)
    await db_session.commit()

    with _require_session_auth(True):
        response = await client.get(f"/admin/leads/{lead.id}", headers=member_headers)
    assert response.status_code == 404


async def test_update_lead_cannot_manually_reassign(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Assignment is automatic-only (see core/tools.py::transfer_to_human's
    round robin) — PATCH /admin/leads/{id} has no claimed_by_account_user_id
    field, so an admin sending one is silently ignored rather than acting on
    it, and the lead stays unassigned."""
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000024")
    db_session.add(user)
    await db_session.flush()

    admin_headers = await _login_as(client, db_session, email="admin2@example.com", role="admin")
    member_headers = await _login_as(
        client, db_session, email="member4@example.com", role="member"
    )
    member_id = (
        await db_session.execute(select(AccountUser).where(AccountUser.email == "member4@example.com"))
    ).scalar_one().id
    _ = member_headers  # only needed to create the membership row above

    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="new")
    db_session.add(lead)
    await db_session.commit()

    with _require_session_auth(True):
        response = await client.patch(
            f"/admin/leads/{lead.id}",
            json={"claimed_by_account_user_id": str(member_id)},
            headers=admin_headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["claimed_by_account_user_id"] is None
    assert body["claimed_at"] is None


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
    # Leading UTF-8 BOM is intentional — see _csv_streaming_response in
    # routers/admin.py, needed for Excel to detect non-ASCII text correctly.
    lines = response.text.lstrip("﻿").strip().splitlines()
    assert lines[0] == "id,name,phone,intent,tags,channel,status,qualification_status,qualification_score,created_at"
    assert lines[1].split(",")[3:7] == ["quote", "", "voice", "new"]


async def test_leads_csv_export_starts_with_utf8_bom(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Without a BOM, Excel on Windows guesses the file's encoding from the
    system codepage instead of UTF-8, and any non-ASCII character (accented
    names, ...) renders as mojibake even though the underlying bytes are
    valid UTF-8. See _csv_streaming_response in routers/admin.py."""
    await _seed_org(db_session)

    response = await client.get("/admin/leads.csv", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")


async def test_leads_csv_includes_tags_and_filters_by_tag(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000012")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="a", tags=["hot", "enterprise"]))
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, intent="b", tags=["cold"]))
    await db_session.commit()

    response = await client.get("/admin/leads.csv", params={"tag": "hot"}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 2
    assert '"hot,enterprise"' in lines[1] or "hot,enterprise" in lines[1]


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
    assert lines[1].split(",")[6] == "qualified"


async def test_import_leads_csv_stages_campaign_and_creates_qualified_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Leads-page imports (unlike the Campaigns page) do both: stage the
    usual CallCampaign + CampaignTarget rows for AI outreach (campaign
    behavior is unchanged), AND immediately create a Lead per contact using
    each row's own 'status' column — an org uploading a trusted contact list
    shouldn't have to wait for the AI to call each one before seeing them on
    the Leads page."""
    await _seed_org(db_session)
    csv_body = (
        "name,phone,intent,status\n"
        "Asha,+910000000010,Book a demo,qualified\n"
        "Ravi,+910000000011,,qualified\n"
    )

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    assert body["campaign"]["channel"] == "voice"
    # New uploads default to "draft" — outreach doesn't start on its own
    # anymore (see apps/api/db/models/call_campaign.py).
    assert body["campaign"]["status"] == "draft"

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert {t.phone for t in targets} == {"+910000000010", "+910000000011"}
    assert all(t.status == "pending" for t in targets)

    leads = (await db_session.execute(select(Lead))).scalars().all()
    assert {lead.phone for lead in leads} == {"+910000000010", "+910000000011"}
    assert all(lead.status == "qualified" for lead in leads)
    assert all(lead.intent == "imported" for lead in leads)


async def test_import_leads_csv_accepts_custom_campaign_name_and_channel(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = "name,phone,status\nAsha,+910000000012,new\n"

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
    csv_body = "name,phone,status\nNo Phone,,new\nBad Format,9179609989,new\n"

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "voice"},
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


async def test_import_leads_csv_unrouted_rows_reported_not_defaulted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Regression guard for the silent-default-to-voice bug: a row with no
    'call'/'whatsapp' columns and no 'channel' column, uploaded without a
    forced channel, must be reported as a per-row error — not silently
    routed to voice (which used to make "WhatsApp list uploaded, nothing
    sent" a silent failure)."""
    await _seed_org(db_session)
    csv_body = "name,phone,status\nAsha,+910000000014,new\n"

    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400

    targets = (await db_session.execute(select(CampaignTarget))).scalars().all()
    assert targets == []


async def test_import_leads_csv_mixed_call_whatsapp_columns_creates_one_campaign(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """One upload, per-row call/whatsapp columns — some rows call-only, some
    WhatsApp-only, some both — produces exactly ONE campaign, marked
    'mixed', with one CampaignTarget per (row, channel) pair. A row marked
    both channels produces two targets under that same campaign."""
    await _seed_org(db_session)
    csv_body = (
        "name,phone,call,whatsapp,status\n"
        "CallOnly,+910000000015,yes,no,new\n"
        "WhatsAppOnly,+910000000016,no,yes,new\n"
        "Both,+910000000017,yes,yes,new\n"
        "Neither,+910000000018,no,no,new\n"
    )

    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 4  # "Both" row counts twice, once per channel
    assert body["skipped"] == 1  # the "Neither" row
    assert len(body["campaigns"]) == 1
    assert body["campaigns"][0]["channel"] == "mixed"
    assert body["campaign"]["channel"] == "mixed"

    campaign = (await db_session.execute(select(CallCampaign))).scalar_one()
    assert campaign.channel == "mixed"
    targets = (
        await db_session.execute(
            select(CampaignTarget).where(CampaignTarget.campaign_id == campaign.id)
        )
    ).scalars().all()
    by_phone_channel = {(t.phone, t.channel) for t in targets}
    assert by_phone_channel == {
        ("+910000000015", "voice"),
        ("+910000000016", "whatsapp"),
        ("+910000000017", "voice"),
        ("+910000000017", "whatsapp"),
    }


async def test_import_leads_csv_decodes_cp1252_fallback(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A CSV saved by Excel on Windows (the common real-world case) is
    typically cp1252, not UTF-8 — a name with a curly apostrophe or an
    accented character used to crash the import with an unhandled
    UnicodeDecodeError (500) instead of importing cleanly. See
    _decode_csv_bytes in routers/admin.py."""
    await _seed_org(db_session)
    csv_bytes = "name,phone,status\nO’Brien,+919179609989,new\n".encode("cp1252")

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "voice"},
        files={"file": ("leads.csv", csv_bytes, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 1
    assert body["skipped"] == 0


async def test_import_leads_csv_rejects_missing_phone_column(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", "name,intent,status\nAsha,demo,new\n", "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400


async def test_import_leads_csv_rejects_missing_status_column(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Unlike POST /admin/campaigns (which never touches Lead.status), this
    endpoint creates a Lead per row immediately, so 'status' is required —
    same as 'phone' — rather than silently defaulting."""
    await _seed_org(db_session)
    response = await client.post(
        "/admin/leads/import",
        files={"file": ("leads.csv", "name,phone\nAsha,+910000000019\n", "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert "status" in response.json()["detail"]


async def test_import_leads_csv_uses_per_row_status_and_rejects_invalid_values(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    csv_body = (
        "name,phone,status\n"
        "New Lead,+910000000022,new\n"
        "Contacted Lead,+910000000023,Converted\n"  # case-insensitive
        "Bad Status,+910000000024,not-a-status\n"
    )

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "voice"},
        files={"file": ("leads.csv", csv_body, "text/csv")},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert "status must be one of" in body["errors"][0]["reason"]

    leads = (await db_session.execute(select(Lead))).scalars().all()
    by_phone = {lead.phone: lead.status for lead in leads}
    assert by_phone == {
        "+910000000022": "new",
        "+910000000023": "converted",
    }


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


async def test_import_leads_xlsx_stages_campaign_and_creates_qualified_leads(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    xlsx_bytes = _make_xlsx_bytes(
        [
            ["name", "phone", "intent", "status"],
            ["Asha", "+910000000020", "Book a demo", "qualified"],
            ["Ravi", "+910000000021", "", "qualified"],
        ]
    )

    response = await client.post(
        "/admin/leads/import",
        params={"channel": "voice"},
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
    assert {lead.phone for lead in leads} == {"+910000000020", "+910000000021"}
    assert all(lead.status == "qualified" for lead in leads)


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


async def test_import_leads_bulk_json_stages_campaign_and_creates_qualified_leads(
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
    assert {lead.phone for lead in leads} == {"+910000000030", "+910000000031"}
    assert all(lead.status == "qualified" for lead in leads)


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


async def test_claim_escalation_success(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """First claim sets claimed_by_account_user_id/claimed_at and returns
    the claimant's display name; a second claim by the same caller is a
    no-op success (idempotent), not a conflict."""
    from apps.api.db.models import AccountUser
    from apps.api.deps import DEFAULT_OWNER_ID

    await _seed_org(db_session)
    db_session.add(
        AccountUser(id=DEFAULT_OWNER_ID, email="owner@example.com", token_hash="owner-hash", full_name="Owner")
    )
    user = User(org_id=ORG_ID, phone="+910000000006")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, intent="escalation", channel="whatsapp")
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)

    response = await client.patch(f"/admin/escalations/{lead.id}/claim", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["claimed_by_account_user_id"] == str(DEFAULT_OWNER_ID)
    assert body["claimed_by_name"] == "Owner"
    assert body["claimed_at"] is not None

    # Idempotent re-claim by the same caller.
    response2 = await client.patch(f"/admin/escalations/{lead.id}/claim", headers=ADMIN_HEADERS)
    assert response2.status_code == 200
    assert response2.json()["claimed_by_account_user_id"] == str(DEFAULT_OWNER_ID)


async def test_claim_escalation_conflict_when_already_claimed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.db.models import AccountUser
    from apps.api.deps import DEFAULT_OWNER_ID

    await _seed_org(db_session)
    other_id = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    db_session.add_all(
        [
            AccountUser(id=DEFAULT_OWNER_ID, email="owner@example.com", token_hash="owner-hash"),
            AccountUser(id=other_id, email="other@example.com", token_hash="other-hash", full_name="Other Rep"),
        ]
    )
    user = User(org_id=ORG_ID, phone="+910000000007")
    db_session.add(user)
    await db_session.flush()
    lead = Lead(
        org_id=ORG_ID,
        user_id=user.id,
        intent="escalation",
        channel="whatsapp",
        claimed_by_account_user_id=other_id,
    )
    db_session.add(lead)
    await db_session.commit()
    await db_session.refresh(lead)

    response = await client.patch(f"/admin/escalations/{lead.id}/claim", headers=ADMIN_HEADERS)
    assert response.status_code == 409
    assert "Other Rep" in response.json()["detail"]


async def test_claim_escalation_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    from apps.api.db.models import AccountUser
    from apps.api.deps import DEFAULT_OWNER_ID

    await _seed_org(db_session)
    db_session.add(
        AccountUser(id=DEFAULT_OWNER_ID, email="owner@example.com", token_hash="owner-hash")
    )
    await db_session.commit()

    response = await client.patch(
        f"/admin/escalations/{uuid.uuid4()}/claim", headers=ADMIN_HEADERS
    )
    assert response.status_code == 404


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


async def test_update_org_numbers_sets_both_providers_independently(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An org can now have a dedicated number on BOTH Plivo and Twilio at
    once — setting one must never clear the other (the old behavior, back
    when a single auto-detected field only ever populated one column)."""
    await _seed_org(db_session)

    response = await client.put(
        "/admin/org-numbers",
        json={
            "phone_numbers": [
                {"provider": "plivo", "phone_number": "+15550001111"},
                {"provider": "twilio", "phone_number": "+15550002222"},
            ]
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    numbers = {n["provider"]: n["phone_number"] for n in body["phone_numbers"]}
    assert numbers == {"plivo": "15550001111", "twilio": "15550002222"}

    # `phone_numbers` omitted entirely leaves the org's numbers untouched.
    response2 = await client.put(
        "/admin/org-numbers",
        json={"whatsapp_phone_number_id": "wa-123"},
        headers=ADMIN_HEADERS,
    )
    assert response2.status_code == 200
    body2 = response2.json()
    numbers2 = {n["provider"]: n["phone_number"] for n in body2["phone_numbers"]}
    assert numbers2 == {"plivo": "15550001111", "twilio": "15550002222"}

    # Sending phone_numbers again REPLACES the full set — e.g. two Plivo
    # numbers now, Twilio dropped.
    response3 = await client.put(
        "/admin/org-numbers",
        json={
            "phone_numbers": [
                {"provider": "plivo", "phone_number": "+15550003333", "is_default": True},
                {"provider": "plivo", "phone_number": "+15550004444"},
            ]
        },
        headers=ADMIN_HEADERS,
    )
    assert response3.status_code == 200
    body3 = response3.json()
    plivo_numbers = [n for n in body3["phone_numbers"] if n["provider"] == "plivo"]
    assert {n["phone_number"] for n in plivo_numbers} == {"15550003333", "15550004444"}
    assert not any(n["provider"] == "twilio" for n in body3["phone_numbers"])
    assert next(n for n in plivo_numbers if n["phone_number"] == "15550003333")["is_default"] is True


async def test_outbound_call_passes_chosen_provider_through(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dashboard's provider toggle (only shown when an org has both a
    dedicated Plivo and Twilio number) must reach failover.initiate_call as
    an explicit preference, not just the automatic from-number inference."""
    from apps.api.routers import admin as admin_module

    await _seed_org(db_session)
    db_session.add_all(
        [
            OrgPhoneNumber(org_id=ORG_ID, provider="plivo", phone_number="15550001111", is_default=True),
            OrgPhoneNumber(org_id=ORG_ID, provider="twilio", phone_number="15550002222", is_default=True),
        ]
    )
    await db_session.commit()

    monkeypatch.setattr(admin_module.voice_failover, "is_configured", lambda: True)

    captured: dict[str, object] = {}

    async def _fake_initiate_call(*args: object, **kwargs: object) -> tuple[dict, str]:
        captured.update(kwargs)
        return {"sid": "CA123"}, "twilio"

    monkeypatch.setattr(admin_module.voice_failover, "initiate_call", _fake_initiate_call)

    response = await client.post(
        "/admin/outbound/call",
        json={"to_phone": "+919876543210", "provider": "twilio"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert captured["preferred_provider"] == "twilio"


async def test_update_calling_settings_persists_preferred_provider(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)

    put_response = await client.put(
        "/admin/settings/calling", json={"preferred_provider": "twilio"}, headers=ADMIN_HEADERS
    )
    assert put_response.status_code == 200
    assert put_response.json()["preferred_provider"] == "twilio"

    get_response = await client.get("/admin/settings/calling", headers=ADMIN_HEADERS)
    assert get_response.json()["preferred_provider"] == "twilio"

    # null clears it back to automatic.
    clear_response = await client.put(
        "/admin/settings/calling", json={"preferred_provider": None}, headers=ADMIN_HEADERS
    )
    assert clear_response.json()["preferred_provider"] is None


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
