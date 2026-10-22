"""Phase 8H: the next call to a number that already has pilot records starts from a clean slate.

The pilot database keeps its earlier user, lead, conversations, messages and appointment (they are preserved on purpose).
These tests prove, offline, that a new call to the same number opens a NEW conversation, replays none of the old history
into a reconnect or into the prompt, leaves the old appointment untouched, and only treats the old appointment as an
ordinary occupied slot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from apps.api.channels.voice import adapter
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.config import settings
from apps.api.core.tools import find_conflicting_appointment
from apps.api.db.models import Appointment, Conversation, Lead, Message, Org, User

ORG = uuid.UUID("00000000-0000-0000-0000-000000000001")
PHONE = "+910000000001"
OLD_LINE = "OLD-CALL-LINE-THE-AGENT-MUST-NOT-REPEAT"


@pytest.fixture
async def earlier_pilot_data(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    org = Org(id=ORG, name="Veerox Pilot")
    user = User(org_id=ORG, phone=PHONE)
    db_session.add_all([org, user])
    await db_session.flush()
    lead = Lead(org_id=ORG, user_id=user.id, phone=PHONE, channel="voice", status="new")
    old = Conversation(org_id=ORG, user_id=user.id, channel="voice", plivo_call_uuid="old-call", ended_at=datetime.now(UTC))
    db_session.add_all([lead, old])
    await db_session.flush()
    for i, role in enumerate(("assistant", "user", "assistant", "user")):
        db_session.add(Message(org_id=ORG, conversation_id=old.id, user_id=user.id, role=role, content=f"{OLD_LINE} {i}", channel="voice"))
    slot = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    appointment = Appointment(org_id=ORG, lead_id=lead.id, scheduled_at=slot, duration_minutes=30, status="scheduled")
    db_session.add(appointment)
    await db_session.commit()

    class _Ctx:
        async def __aenter__(self) -> Any:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(adapter, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(settings, "voice_reconnect_context_turns", 6)
    return SimpleNamespace(user=user, lead=lead, old=old, appointment=appointment, slot=slot)


async def _count(db: Any, model: Any) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def test_a_new_call_opens_a_new_conversation_for_the_same_person(db_session: Any, earlier_pilot_data: SimpleNamespace) -> None:
    user_id, conversation_id = await adapter.open_voice_conversation(PHONE, "new-call", ORG)
    assert user_id == earlier_pilot_data.user.id  # the same person is recognised
    assert conversation_id != earlier_pilot_data.old.id  # but this call is its own conversation
    assert await _count(db_session, Conversation) == 2
    assert await _count(db_session, User) == 1 and await _count(db_session, Lead) == 1  # no duplicate person or lead


async def test_nothing_from_the_earlier_call_is_replayed_into_the_new_one(db_session: Any, earlier_pilot_data: SimpleNamespace) -> None:
    user_id, conversation_id = await adapter.open_voice_conversation(PHONE, "new-call", ORG)
    state = adapter.CallState(user_id=user_id, conversation_id=conversation_id, org_id=ORG, call_uuid="new-call")
    assert await adapter.build_reconnect_items(state) == []  # a reconnect in the new call rebuilds an empty history
    await db_session.refresh(earlier_pilot_data.old)
    assert await _count(db_session, Message) == 4  # the earlier messages are preserved, untouched


async def test_the_prompt_for_the_new_call_carries_no_earlier_call_text(earlier_pilot_data: SimpleNamespace) -> None:
    instructions = await bridge._system_instructions(None, ORG, None)
    assert OLD_LINE not in instructions


async def test_the_earlier_appointment_is_not_reused_or_changed_by_a_new_call(db_session: Any, earlier_pilot_data: SimpleNamespace) -> None:
    await adapter.open_voice_conversation(PHONE, "new-call", ORG)
    assert await _count(db_session, Appointment) == 1
    row = (await db_session.execute(select(Appointment))).scalar_one()
    assert row.id == earlier_pilot_data.appointment.id and row.status == "scheduled" and row.scheduled_at.replace(tzinfo=UTC) == earlier_pilot_data.slot


async def test_the_earlier_appointment_only_occupies_its_own_slot(db_session: Any, earlier_pilot_data: SimpleNamespace) -> None:
    slot = earlier_pilot_data.slot
    assert await find_conflicting_appointment(db_session, ORG, slot) is not None  # that exact time is taken
    assert await find_conflicting_appointment(db_session, ORG, slot + timedelta(hours=3)) is None  # any other time is free
