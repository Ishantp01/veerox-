"""Backup before a merge, rollback after it: the merge deletes duplicate rows, so
the snapshot must bring every one of them (and every re-pointed row) back."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.chat_backup import (
    load_backup,
    plan_restore,
    restore_snapshot,
    take_snapshot,
    write_backup,
)
from apps.api.core.chat_merge import merge_customer
from apps.api.core.user_merge import merge_contact
from apps.api.db.models import Conversation, Lead, Message, Org, User

pytestmark = pytest.mark.asyncio

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


async def _count(db: AsyncSession, model) -> int:
    return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


async def test_rollback_restores_whatsapp_merge(db_session: AsyncSession, tmp_path) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    user = User(org_id=ORG_ID, phone="919100000001", created_at=T0)
    db_session.add(user)
    await db_session.flush()
    first = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0, tags=["a"], summary="one")
    second = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0 + timedelta(days=2), tags=["b"], summary="two")
    db_session.add_all([first, second])
    await db_session.flush()
    msgs = [
        Message(org_id=ORG_ID, conversation_id=first.id, user_id=user.id, role="user", content="m1", channel="whatsapp"),
        Message(org_id=ORG_ID, conversation_id=second.id, user_id=user.id, role="user", content="m2", channel="whatsapp"),
    ]
    db_session.add_all(msgs)
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, conversation_id=second.id))
    await db_session.commit()
    first_id, second_id, m2_id = first.id, second.id, msgs[1].id

    info = await write_backup(db_session, [user.id], "test", tmp_path)
    assert info.path.exists() and info.tables["conversations"] == 2 and info.tables["messages"] == 2

    await merge_customer(db_session, ORG_ID, user.id)
    await db_session.commit()
    assert await _count(db_session, Conversation) == 1  # the duplicate is gone

    snapshot = load_backup(info.path)
    plan = await plan_restore(db_session, snapshot)
    assert plan.missing["conversations"] == 1 and plan.changed["messages"] == 1

    await restore_snapshot(db_session, snapshot)
    await db_session.commit()
    db_session.expire_all()

    assert await _count(db_session, Conversation) == 2
    assert (await db_session.get(Message, m2_id)).conversation_id == second_id
    assert (await db_session.get(Conversation, first_id)).summary == "one"  # merged summary undone
    assert (await db_session.get(Lead, (await db_session.execute(select(Lead.id))).scalar_one())).conversation_id == second_id
    assert not (await plan_restore(db_session, snapshot)).needs_restore


async def test_rollback_restores_contact_merge_and_keeps_newer_rows(
    db_session: AsyncSession, tmp_path
) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    busy = User(org_id=ORG_ID, phone="919100000002", created_at=T0)
    thin = User(org_id=ORG_ID, phone="+919100000002", name="Asha", created_at=T0 + timedelta(days=1))
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
    db_session.add(Lead(org_id=ORG_ID, user_id=thin.id, name="Asha"))
    await db_session.commit()
    busy_id, thin_id, call_id, wa_id = busy.id, thin.id, call.id, wa.id

    info = await write_backup(db_session, [busy_id, thin_id], "contacts", tmp_path)
    await merge_contact(db_session, ORG_ID, "919100000002")
    await db_session.commit()
    assert await _count(db_session, User) == 1

    # A message that arrives after the merge must survive the rollback.
    survivor_id = (await db_session.execute(select(User.id))).scalar_one()
    db_session.add(
        Message(org_id=ORG_ID, conversation_id=wa_id, user_id=survivor_id, role="user", content="later", channel="whatsapp")
    )
    await db_session.commit()

    await restore_snapshot(db_session, load_backup(info.path))
    await db_session.commit()
    db_session.expire_all()

    assert await _count(db_session, User) == 2
    assert (await db_session.get(Conversation, call_id)).user_id == thin_id
    assert await _count(db_session, Message) == 3  # 2 original + the newer one
    assert (await db_session.get(User, thin_id)).name == "Asha"


async def test_snapshot_covers_only_the_requested_customers(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    mine = User(org_id=ORG_ID, phone="919100000003")
    other = User(org_id=ORG_ID, phone="919100000004")
    db_session.add_all([mine, other])
    await db_session.flush()
    for u in (mine, other):
        c = Conversation(org_id=ORG_ID, user_id=u.id, channel="whatsapp", started_at=T0)
        db_session.add(c)
        await db_session.flush()
        db_session.add(Message(org_id=ORG_ID, conversation_id=c.id, user_id=u.id, role="user", content="x", channel="whatsapp"))
    await db_session.commit()

    snapshot = await take_snapshot(db_session, [mine.id])
    assert [r["id"] for r in snapshot["users"]] == [mine.id]
    assert len(snapshot["messages"]) == 1 and snapshot["messages"][0]["user_id"] == mine.id
