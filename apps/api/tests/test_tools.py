"""Tests for apps.api.core.tools — the four agent tool handlers.

Redis is monkeypatched with an in-process fake so tests are hermetic. The
handlers' SQL writes hit the test SQLite session from conftest.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core import tools
from apps.api.core.tools import (
    book_appointment,
    capture_lead,
    lookup_customer,
    qualify_lead,
    transfer_to_human,
)
from apps.api.db.models import CallCampaign, CampaignTarget, Lead, Org, User

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeRedis:
    """Minimal Redis stand-in for the bits the tool handlers touch.

    Implements just ``set(... nx=, ex=)``, ``rpush``, ``get``.
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self.kv:
            return None  # mirrors redis-py: None when SETNX would not set
        self.kv[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Patch get_redis_pool to return a fresh fake per test."""
    fake = _FakeRedis()
    monkeypatch.setattr(tools, "get_redis_pool", lambda: fake)
    return fake


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def test_capture_lead_persists_row_and_returns_ok(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)

    result = await capture_lead(
        db_session, phone="98765 43210", intent="gym membership", name="Asha"
    )

    assert result["status"] == "ok"
    assert "lead_id" in result

    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert len(rows) == 1
    assert rows[0].intent == "gym membership"
    # Phone normalised — non-digits stripped.
    assert rows[0].phone == "9876543210"


async def test_capture_lead_idempotent_within_window(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """Second call with same (phone, intent) returns duplicate, writes nothing extra."""
    await _seed_org(db_session)

    first = await capture_lead(
        db_session, phone="9999999999", intent="quote", name="A"
    )
    second = await capture_lead(
        db_session, phone="9999999999", intent="quote", name="A"
    )

    assert first["status"] == "ok"
    assert second["status"] == "duplicate"

    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert len(rows) == 1


async def test_capture_lead_persists_channel_when_provided(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)

    result = await capture_lead(
        db_session,
        phone="9876543211",
        intent="quote",
        name="Bala",
        channel="whatsapp",
    )

    assert result["status"] == "ok"
    row = (await db_session.execute(select(Lead))).scalars().one()
    assert row.channel == "whatsapp"


async def test_book_appointment_persists_channel_when_provided(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000098", name="Caller")
    db_session.add(user)
    await db_session.commit()

    result = await book_appointment(
        db_session,
        user_id=str(user.id),
        date="2026-08-01",
        time="10:00",
        channel="voice",
    )

    assert result["status"] == "ok"
    row = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().one()
    assert row.channel == "voice"


async def test_transfer_to_human_enqueues_and_writes_lead(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000099", name="Caller")
    db_session.add(user)
    await db_session.commit()

    result = await transfer_to_human(
        db_session,
        reason="needs human help",
        urgency="high",
        user_id=user.id,
        channel="voice",
    )

    assert result["status"] == "ok"
    assert result["lead_id"]  # was written because user_id was supplied

    # Lead row exists with intent=escalation and metadata captured.
    rows = (
        await db_session.execute(
            select(Lead).where(Lead.intent == "escalation")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].metadata_ == {"reason": "needs human help", "urgency": "high"}
    assert rows[0].channel == "voice"

    # Redis queue got a JSON entry, tagged with the channel.
    queue = fake_redis.lists.get("human_handoff_queue", [])
    assert len(queue) == 1
    entry = json.loads(queue[0])
    assert entry["reason"] == "needs human help"
    assert entry["urgency"] == "high"
    assert entry["channel"] == "voice"


async def test_transfer_to_human_without_user_id_only_enqueues(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """No user_id (LLM args alone) → queue still gets the entry, no Lead row."""
    await _seed_org(db_session)

    result = await transfer_to_human(db_session, reason="x", urgency="low")

    assert result["status"] == "ok"
    assert result["lead_id"] is None

    rows = (await db_session.execute(select(Lead))).scalars().all()
    assert rows == []
    assert len(fake_redis.lists["human_handoff_queue"]) == 1


async def _seed_campaign_target(db: AsyncSession, phone: str = "9000000001") -> CampaignTarget:
    campaign = CallCampaign(org_id=ORG_ID, name="Test Campaign", criteria="Wants a demo")
    db.add(campaign)
    await db.flush()
    target = CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone=phone)
    db.add(target)
    await db.commit()
    return target


async def test_qualify_lead_without_campaign_target_is_noop(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)

    result = await qualify_lead(db_session, interested=True, reason="great fit")

    assert result == {"status": "error", "reason": "no_campaign_target"}
    assert (await db_session.execute(select(Lead))).scalars().all() == []


async def test_qualify_lead_interested_writes_lead_and_completes_target(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    target = await _seed_campaign_target(db_session)

    result = await qualify_lead(
        db_session,
        interested=True,
        reason="confirmed budget and timeline",
        name="Priya",
        campaign_target_id=target.id,
        channel="voice",
    )

    assert result["status"] == "ok"
    assert result["lead_id"]

    await db_session.refresh(target)
    assert target.status == "completed"
    assert target.qualified is True
    assert target.disposition_reason == "confirmed budget and timeline"

    lead = (await db_session.execute(select(Lead))).scalars().one()
    assert lead.status == "qualified"
    assert lead.intent == "qualified_campaign_lead"
    assert lead.phone == target.phone
    assert lead.metadata_["campaign_id"] == str(target.campaign_id)


async def test_qualify_lead_not_interested_writes_no_lead(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    target = await _seed_campaign_target(db_session, phone="9000000002")

    result = await qualify_lead(
        db_session,
        interested=False,
        reason="no budget",
        campaign_target_id=target.id,
    )

    assert result == {"status": "ok", "interested": False, "lead_id": None}

    await db_session.refresh(target)
    assert target.status == "completed"
    assert target.qualified is False
    assert target.disposition_reason == "no budget"

    assert (await db_session.execute(select(Lead))).scalars().all() == []


async def test_lookup_customer_returns_found_false_on_miss(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    result = await lookup_customer(db_session, phone="+910000000000")
    assert result == {"found": False}


async def test_lookup_customer_returns_user_when_present(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+919876543210", name="Existing")
    db_session.add(user)
    await db_session.commit()

    result = await lookup_customer(db_session, phone="+91 98765 43210")

    assert result["found"] is True
    assert result["name"] == "Existing"
    assert result["phone"] == "+919876543210"
