"""One person must be one contact: ``+91…`` and ``91…`` resolve to the same row,
and historical duplicates merge without losing a message, conversation or lead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.user_lookup import find_user_by_phone, phone_forms
from apps.api.core.user_merge import find_duplicate_contacts, merge_contact
from apps.api.db.models import (
    Appointment,
    Conversation,
    ConversationMemory,
    FollowUpTask,
    Lead,
    Message,
    Org,
    User,
)

pytestmark = pytest.mark.asyncio

ORG_ID = __import__("uuid").UUID("00000000-0000-0000-0000-000000000001")
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def test_phone_forms_cover_both_spellings() -> None:
    assert phone_forms("+91 91796-09988") == ["919179609988", "+919179609988"]
    assert phone_forms("919179609988") == ["919179609988", "+919179609988"]


async def test_lookup_finds_the_same_contact_in_either_spelling(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    plain = User(org_id=ORG_ID, phone="919179609988")
    db_session.add(plain)
    await db_session.commit()

    assert (await find_user_by_phone(db_session, ORG_ID, "+919179609988")).id == plain.id
    assert (await find_user_by_phone(db_session, ORG_ID, "919179609988")).id == plain.id
    assert await find_user_by_phone(db_session, ORG_ID, "+919000000000") is None


async def test_merge_keeps_everything_and_resolves_conflicts(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    busy = User(org_id=ORG_ID, phone="919179609988", created_at=T0)
    thin = User(org_id=ORG_ID, phone="+919179609988", name="Asha", created_at=T0 + timedelta(days=1))
    other_person = User(org_id=ORG_ID, phone="919000000001")
    db_session.add_all([busy, thin, other_person])
    await db_session.flush()

    wa = Conversation(org_id=ORG_ID, user_id=busy.id, channel="whatsapp", started_at=T0)
    call = Conversation(org_id=ORG_ID, user_id=thin.id, channel="voice", started_at=T0 + timedelta(days=1))
    db_session.add_all([wa, call])
    await db_session.flush()
    db_session.add_all(
        [
            Message(org_id=ORG_ID, conversation_id=wa.id, user_id=busy.id, role="user", content="a", channel="whatsapp"),
            Message(org_id=ORG_ID, conversation_id=wa.id, user_id=busy.id, role="assistant", content="b", channel="whatsapp"),
            Message(org_id=ORG_ID, conversation_id=call.id, user_id=thin.id, role="user", content="on call", channel="voice"),
        ]
    )
    keep_lead = Lead(org_id=ORG_ID, user_id=busy.id, status="contacted", name=None)
    drop_lead = Lead(
        org_id=ORG_ID, user_id=thin.id, name="Asha", intent="demo", tags=["hot"],
        claimed_by_account_user_id=None,
    )
    db_session.add_all([keep_lead, drop_lead])
    await db_session.flush()
    appt = Appointment(org_id=ORG_ID, lead_id=drop_lead.id, scheduled_at=T0 + timedelta(days=5))
    kept_task = FollowUpTask(org_id=ORG_ID, lead_id=keep_lead.id, run_at=T0, status="pending")
    clashing_task = FollowUpTask(org_id=ORG_ID, lead_id=drop_lead.id, run_at=T0, status="pending")
    movable_task = FollowUpTask(
        org_id=ORG_ID, lead_id=drop_lead.id, run_at=T0, status="pending", template_name="appointment_reminder"
    )
    db_session.add_all(
        [
            appt, kept_task, clashing_task, movable_task,
            ConversationMemory(org_id=ORG_ID, user_id=busy.id, scope="user", key="city", value="Pune"),
            ConversationMemory(org_id=ORG_ID, user_id=thin.id, scope="user", key="city", value="Delhi"),
            ConversationMemory(org_id=ORG_ID, user_id=thin.id, scope="user", key="budget", value="5k"),
        ]
    )
    await db_session.commit()

    assert await find_duplicate_contacts(db_session) == [(ORG_ID, "919179609988", 2)]
    result = await merge_contact(db_session, ORG_ID, "919179609988")
    await db_session.commit()

    assert result is not None and result.survivor_id == busy.id and result.merged_users == 1
    assert result.messages == 3 and result.conversations == 2

    # One contact left for that number; the unrelated person is untouched.
    phones = sorted((await db_session.execute(select(User.phone))).scalars().all())
    assert phones == ["919000000001", "919179609988"]
    survivor = (await db_session.execute(select(User).where(User.id == busy.id))).scalar_one()
    assert survivor.name == "Asha"  # filled in from the duplicate

    # Both channels now live under the one contact.
    channels = sorted(
        (await db_session.execute(select(Conversation.channel).where(Conversation.user_id == busy.id))).scalars().all()
    )
    assert channels == ["voice", "whatsapp"]
    assert (
        await db_session.execute(select(func.count(Message.id)).where(Message.user_id == busy.id))
    ).scalar_one() == 3

    # One lead, enriched from the dropped one, with its dependents moved over.
    leads = (await db_session.execute(select(Lead).where(Lead.org_id == ORG_ID))).scalars().all()
    assert [l.id for l in leads] == [keep_lead.id]
    await db_session.refresh(keep_lead)
    assert (keep_lead.name, keep_lead.intent, keep_lead.tags, keep_lead.status) == (
        "Asha", "demo", ["hot"], "contacted",
    )
    await db_session.refresh(appt)
    assert appt.lead_id == keep_lead.id
    task_leads = (await db_session.execute(select(FollowUpTask.lead_id))).scalars().all()
    assert sorted(map(str, task_leads)) == sorted([str(keep_lead.id)] * 2)  # clashing built-in task dropped

    # Memory: survivor's value wins, the other side's extra fact is kept.
    facts = {
        m.key: m.value
        for m in (await db_session.execute(select(ConversationMemory).where(ConversationMemory.user_id == busy.id))).scalars()
    }
    assert facts == {"city": "Pune", "budget": "5k"}

    assert await find_duplicate_contacts(db_session) == []
    assert await merge_contact(db_session, ORG_ID, "919179609988") is None
