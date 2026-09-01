"""Tests for apps.api.core.tools — the four agent tool handlers.

Redis is monkeypatched with an in-process fake so tests are hermetic. The
handlers' SQL writes hit the test SQLite session from conftest.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core import tools
from apps.api.core.tools import (
    book_appointment,
    capture_lead,
    initiate_ai_call,
    lookup_customer,
    qualify_lead,
    transfer_to_human,
)
from apps.api.db.models import (
    AccountUser,
    Appointment,
    CallCampaign,
    CampaignTarget,
    Conversation,
    FollowUpTask,
    Lead,
    Org,
    OrgMembership,
    User,
)

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

    future = datetime.now(UTC) + timedelta(days=7)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="10:00",
        timezone="UTC",
        channel="voice",
    )

    assert result["status"] == "ok"
    row = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().one()
    assert row.channel == "voice"

    appointment = (
        await db_session.execute(select(Appointment).where(Appointment.lead_id == row.id))
    ).scalars().one()
    expected = f"{future.date().isoformat()}T10:00:00"
    assert appointment.scheduled_at.replace(tzinfo=None).isoformat() == expected


async def test_book_appointment_rejects_stale_user_id(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """A user_id that doesn't resolve to a real User row (stale/invalid
    caller context) must be rejected rather than writing a Lead with no
    phone number to reach the person by (see core/tools.py's
    book_appointment docstring on ``missing_phone``)."""
    await _seed_org(db_session)

    future = datetime.now(UTC) + timedelta(days=7)
    result = await book_appointment(
        db_session,
        user_id=uuid.uuid4(),
        name="Ghost",
        date=future.date().isoformat(),
        time="10:00",
        timezone="UTC",
        channel="whatsapp",
    )

    assert result == {"status": "error", "reason": "missing_phone"}
    rows = (await db_session.execute(select(Lead).where(Lead.intent == "booking"))).scalars().all()
    assert rows == []


async def test_book_appointment_rejects_missing_name(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """A user with no name on file and no name argument must be rejected —
    a booking with no name attached is useless for human follow-up (see
    core/tools.py's book_appointment docstring)."""
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000093")
    db_session.add(user)
    await db_session.commit()

    future = datetime.now(UTC) + timedelta(days=7)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="10:00",
        timezone="UTC",
        channel="whatsapp",
    )

    assert result == {"status": "error", "reason": "missing_name"}
    rows = (await db_session.execute(select(Lead).where(Lead.intent == "booking"))).scalars().all()
    assert rows == []


async def test_book_appointment_saves_provided_name_onto_user(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name passed to book_appointment (e.g. just given by the caller) is
    used for the Lead and persisted onto the User row for future turns."""
    async def _noop_send_template(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(tools.wa_client, "send_template", _noop_send_template)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000092")
    db_session.add(user)
    await db_session.commit()

    future = datetime.now(UTC) + timedelta(days=7)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        name="Zoya",
        date=future.date().isoformat(),
        time="10:00",
        timezone="UTC",
        channel="whatsapp",
    )

    assert result["status"] == "ok"
    lead = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().one()
    assert lead.name == "Zoya"

    await db_session.refresh(user)
    assert user.name == "Zoya"


async def test_book_appointment_rejects_past_date(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """A past date/time must be rejected rather than silently booked — the
    LLM has no reliable sense of "now" on its own (see core/tools.py's
    book_appointment)."""
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000099", name="Caller")
    db_session.add(user)
    await db_session.commit()

    past = datetime.now(UTC) - timedelta(days=1)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        date=past.date().isoformat(),
        time="10:00",
        channel="whatsapp",
    )

    assert result == {"status": "error", "reason": "date_in_past"}
    count = (await db_session.execute(select(Lead).where(Lead.intent == "booking"))).scalars().all()
    assert count == []


async def test_book_appointment_rejects_slot_within_30_minutes(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second booking within the 30-minute buffer of an existing one must
    be rejected rather than double-booking the slot (see
    find_conflicting_appointment in core/tools.py)."""
    async def _noop_send_template(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(tools.wa_client, "send_template", _noop_send_template)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000097", name="Caller")
    db_session.add(user)
    await db_session.commit()

    future = (datetime.now(UTC) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    first = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="14:00",
        timezone="UTC",
        channel="whatsapp",
    )
    assert first["status"] == "ok"

    second = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="14:25",
        timezone="UTC",
        channel="whatsapp",
    )
    assert second == {"status": "error", "reason": "slot_conflict"}

    # Only the first booking's Lead/Appointment rows exist.
    leads = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().all()
    assert len(leads) == 1


async def test_book_appointment_allows_slot_30_minutes_apart(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly 30 minutes after an existing booking is far enough to allow."""
    async def _noop_send_template(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(tools.wa_client, "send_template", _noop_send_template)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000096", name="Caller")
    db_session.add(user)
    await db_session.commit()

    future = (datetime.now(UTC) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    first = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="14:00",
        timezone="UTC",
        channel="whatsapp",
    )
    assert first["status"] == "ok"

    second = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="14:30",
        timezone="UTC",
        channel="whatsapp",
    )
    assert second["status"] == "ok"


async def test_book_appointment_schedules_three_reminders(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A booking far enough out gets reminder FollowUpTasks at 60/30/5
    minutes before, sent via the pre-approved appointment_reminder template
    (see _APPOINTMENT_REMINDER_OFFSETS_MINUTES in core/tools.py)."""
    async def _noop_send_template(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(tools.wa_client, "send_template", _noop_send_template)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000095", name="Priya")
    db_session.add(user)
    await db_session.commit()

    future = datetime.now(UTC) + timedelta(days=3)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time="15:00",
        timezone="UTC",
        channel="whatsapp",
    )
    assert result["status"] == "ok"

    lead = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().one()
    tasks = (
        await db_session.execute(
            select(FollowUpTask).where(FollowUpTask.lead_id == lead.id).order_by(FollowUpTask.run_at)
        )
    ).scalars().all()

    assert len(tasks) == 3
    appointment = (
        await db_session.execute(select(Appointment).where(Appointment.lead_id == lead.id))
    ).scalars().one()
    expected_offsets_minutes = [60, 30, 5]
    for task, offset in zip(tasks, expected_offsets_minutes):
        assert task.status == "pending"
        assert task.template_name == "appointment_reminder"
        assert task.template_params == ["Priya", future.date().isoformat(), "3:00 PM"]
        assert task.run_at == appointment.scheduled_at - timedelta(minutes=offset)


async def test_book_appointment_skips_reminders_already_in_the_past(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Booking only a few minutes out must not fire the 60/30-minute
    reminders immediately — only offsets still in the future get scheduled."""
    async def _noop_send_template(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(tools.wa_client, "send_template", _noop_send_template)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000094", name="Rahul")
    db_session.add(user)
    await db_session.commit()

    future = datetime.now(UTC) + timedelta(minutes=10)
    result = await book_appointment(
        db_session,
        user_id=user.id,
        date=future.date().isoformat(),
        time=future.strftime("%H:%M"),
        timezone="UTC",
        channel="whatsapp",
    )
    assert result["status"] == "ok"

    lead = (
        await db_session.execute(select(Lead).where(Lead.intent == "booking"))
    ).scalars().one()
    tasks = (
        await db_session.execute(select(FollowUpTask).where(FollowUpTask.lead_id == lead.id))
    ).scalars().all()

    # Only the 5-minute-before reminder is still in the future.
    assert len(tasks) == 1


async def test_transfer_to_human_enqueues_and_writes_lead(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000099", name="Caller")
    db_session.add(user)
    await db_session.commit()

    conversation = Conversation(org_id=ORG_ID, user_id=user.id, channel="voice")
    db_session.add(conversation)
    await db_session.commit()

    result = await transfer_to_human(
        db_session,
        reason="needs human help",
        urgency="high",
        user_id=user.id,
        channel="voice",
        conversation_id=conversation.id,
    )

    assert result["status"] == "ok"
    assert result["lead_id"]  # was written because user_id was supplied

    # Lead row exists with intent=escalation, metadata, phone, and
    # conversation_id all captured.
    rows = (
        await db_session.execute(
            select(Lead).where(Lead.intent == "escalation")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].metadata_ == {"reason": "needs human help", "urgency": "high"}
    assert rows[0].channel == "voice"
    assert rows[0].phone == "+910000000099"
    assert rows[0].conversation_id == conversation.id

    # Redis queue got a JSON entry, tagged with the channel, phone, and
    # conversation_id.
    queue = fake_redis.lists.get("human_handoff_queue", [])
    assert len(queue) == 1
    entry = json.loads(queue[0])
    assert entry["reason"] == "needs human help"
    assert entry["urgency"] == "high"
    assert entry["channel"] == "voice"
    assert entry["phone"] == "+910000000099"
    assert entry["conversation_id"] == str(conversation.id)


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


async def test_transfer_to_human_notifies_org_owner_via_whatsapp(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The org owner's mobile gets the appointment_confirmation template
    with the lead's name/number filling the (name, date, time) slots."""
    sent: list[dict[str, object]] = []

    async def _fake_send_template(to: str, template_name: str, **kwargs: object) -> None:
        sent.append({"to": to, "template_name": template_name, **kwargs})

    monkeypatch.setattr(tools.wa_client, "send_template", _fake_send_template)
    await _seed_org(db_session)

    owner = AccountUser(email="owner@example.com", token_hash="x", mobile="+919999999999")
    db_session.add(owner)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=owner.id, role="admin"))

    lead_user = User(org_id=ORG_ID, phone="+910000000099", name="Asha")
    db_session.add(lead_user)
    await db_session.commit()

    result = await transfer_to_human(
        db_session,
        reason="wants pricing details",
        user_id=lead_user.id,
        channel="whatsapp",
    )

    assert result["status"] == "ok"
    assert len(sent) == 1
    assert sent[0]["to"] == "+919999999999"
    assert sent[0]["template_name"] == "appointment_confirmation"
    assert sent[0]["body_params"] == [
        "Asha",
        "a new lead requesting human follow-up",
        "+910000000099",
    ]


async def test_transfer_to_human_falls_back_to_admin_when_owner_has_no_mobile(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, object]] = []

    async def _fake_send_template(to: str, template_name: str, **kwargs: object) -> None:
        sent.append({"to": to, "template_name": template_name, **kwargs})

    monkeypatch.setattr(tools.wa_client, "send_template", _fake_send_template)
    await _seed_org(db_session)

    owner = AccountUser(email="owner@example.com", token_hash="x", mobile=None)
    admin = AccountUser(email="admin@example.com", token_hash="y", mobile="+918888888888")
    db_session.add_all([owner, admin])
    await db_session.flush()
    db_session.add_all(
        [
            OrgMembership(org_id=ORG_ID, account_user_id=owner.id, role="admin", invited_by_id=None),
            OrgMembership(
                org_id=ORG_ID, account_user_id=admin.id, role="admin", invited_by_id=owner.id
            ),
        ]
    )
    await db_session.commit()

    result = await transfer_to_human(db_session, reason="needs help", org_id=ORG_ID)

    assert result["status"] == "ok"
    assert len(sent) == 1
    assert sent[0]["to"] == "+918888888888"


async def test_transfer_to_human_no_org_contact_skips_send_without_error(
    db_session: AsyncSession, fake_redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No teammate has a mobile on file → best-effort notify is skipped, the
    handoff itself still succeeds."""
    async def _unexpected_send_template(*args: object, **kwargs: object) -> None:
        raise AssertionError("should not attempt a WhatsApp send with no notify phone")

    monkeypatch.setattr(tools.wa_client, "send_template", _unexpected_send_template)
    await _seed_org(db_session)

    result = await transfer_to_human(db_session, reason="needs help", org_id=ORG_ID)

    assert result["status"] == "ok"


async def test_initiate_ai_call_blocked_when_message_asks_for_human(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deterministic backstop for a real misfire: gpt-4o-mini sometimes
    reads "connect me to a human/agent/team member" as a request to be
    called back and picks initiate_ai_call instead of transfer_to_human —
    which would actually ring the lead's own phone. This must never place
    the call regardless of what the model decided to call."""
    async def _unexpected_initiate_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not place a call for an explicit human-handoff request")

    monkeypatch.setattr(tools.voice_failover, "is_configured", lambda: True)
    monkeypatch.setattr(tools.voice_failover, "initiate_call", _unexpected_initiate_call)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000099", name="Caller")
    db_session.add(user)
    await db_session.commit()

    result = await initiate_ai_call(
        db_session,
        user_id=user.id,
        org_id=ORG_ID,
        channel="whatsapp",
        raw_message="please connect me to a human agent",
    )

    assert result["status"] == "error"
    assert result["reason"] == "this_is_a_human_handoff_request_call_transfer_to_human_instead"


async def test_initiate_ai_call_proceeds_for_genuine_callback_request(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain "call me back" with no human/agent wording is unaffected by
    the guard and still places the AI callback as before."""
    async def _fake_initiate_call(*args: object, **kwargs: object) -> tuple[dict, str]:
        return {}, "plivo"

    monkeypatch.setattr(tools.voice_failover, "is_configured", lambda: True)
    monkeypatch.setattr(tools.voice_failover, "initiate_call", _fake_initiate_call)
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000099", name="Caller")
    db_session.add(user)
    await db_session.commit()

    result = await initiate_ai_call(
        db_session,
        user_id=user.id,
        org_id=ORG_ID,
        channel="whatsapp",
        raw_message="can you call me back please",
    )

    assert result["status"] == "ok"


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
