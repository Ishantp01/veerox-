"""One chat thread per lead: every conversation (WhatsApp session or voice call)
of the same customer hangs off the same ChatThread row, from the moment it is
created, and merging duplicate contacts keeps it to one."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.user_merge import merge_contact
from apps.api.db.models import ChatThread, Conversation, Message, Org, User

pytestmark = pytest.mark.asyncio

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


async def _threads(db: AsyncSession) -> list[ChatThread]:
    return list((await db.execute(select(ChatThread))).scalars().all())


async def test_every_conversation_of_a_lead_shares_one_thread(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    asha = User(org_id=ORG_ID, phone="919200000001")
    ravi = User(org_id=ORG_ID, phone="919200000002")
    db_session.add_all([asha, ravi])
    await db_session.flush()

    # A WhatsApp session, a call and another call for Asha; one session for Ravi.
    convs = [
        Conversation(org_id=ORG_ID, user_id=asha.id, channel="whatsapp", started_at=T0),
        Conversation(org_id=ORG_ID, user_id=asha.id, channel="voice", started_at=T0 + timedelta(days=1)),
        Conversation(org_id=ORG_ID, user_id=ravi.id, channel="whatsapp", started_at=T0),
    ]
    db_session.add_all(convs)
    await db_session.commit()
    # A later conversation, created in a separate transaction, joins the same thread.
    later = Conversation(org_id=ORG_ID, user_id=asha.id, channel="voice", started_at=T0 + timedelta(days=3))
    db_session.add(later)
    await db_session.commit()

    threads = await _threads(db_session)
    assert len(threads) == 2  # one per lead, not one per conversation
    by_user = {t.user_id: t.id for t in threads}
    rows = (await db_session.execute(select(Conversation.user_id, Conversation.chat_thread_id))).all()
    assert len(rows) == 4
    assert all(thread_id == by_user[user_id] for user_id, thread_id in rows)


async def test_contact_merge_leaves_one_thread_with_every_conversation(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    busy = User(org_id=ORG_ID, phone="919200000003", created_at=T0)
    thin = User(org_id=ORG_ID, phone="+919200000003", created_at=T0 + timedelta(days=1))
    db_session.add_all([busy, thin])
    await db_session.flush()
    wa = Conversation(org_id=ORG_ID, user_id=busy.id, channel="whatsapp", started_at=T0)
    call = Conversation(org_id=ORG_ID, user_id=thin.id, channel="voice", started_at=T0 + timedelta(days=1))
    db_session.add_all([wa, call])
    await db_session.flush()
    db_session.add_all(
        [
            Message(org_id=ORG_ID, conversation_id=wa.id, user_id=busy.id, role="user", content="a", channel="whatsapp"),
            Message(org_id=ORG_ID, conversation_id=call.id, user_id=thin.id, role="user", content="b", channel="voice"),
        ]
    )
    await db_session.commit()
    assert len(await _threads(db_session)) == 2  # the split that caused two chats

    await merge_contact(db_session, ORG_ID, "919200000003")
    await db_session.commit()

    threads = await _threads(db_session)
    assert len(threads) == 1
    thread = threads[0]
    survivor_id = (await db_session.execute(select(User.id))).scalar_one()
    assert thread.user_id == survivor_id
    ids = (await db_session.execute(select(Conversation.chat_thread_id))).scalars().all()
    assert len(ids) == 2 and set(ids) == {thread.id}
    assert (await db_session.execute(select(func.count()).select_from(Message))).scalar_one() == 2
