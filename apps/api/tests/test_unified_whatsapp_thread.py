"""One WhatsApp conversation per customer: reuse on every new message, merge of
historical duplicates without losing anything, and engine session isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.chat_merge import find_duplicate_groups, merge_customer
from apps.api.core.chat_threads import get_or_create_whatsapp_conversation, session_since
from apps.api.core.conversation.config import resolve_config
from apps.api.core.conversation.engine import _last_state
from apps.api.db.models import (
    CallCampaign,
    CampaignTarget,
    Conversation,
    ConversationTurn,
    Lead,
    Message,
    Org,
    User,
)
from apps.api.tests.test_admin_endpoints import (  # noqa: F401  (fixtures)
    ORG_ID,
    _seed_org,
    client,
    fake_redis,
)

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


async def _customer(db: AsyncSession, phone: str) -> User:
    user = User(org_id=ORG_ID, phone=phone)
    db.add(user)
    await db.flush()
    return user


def _msg(user: User, conv: Conversation, content: str, at: datetime, role: str = "user") -> Message:
    return Message(
        org_id=ORG_ID, conversation_id=conv.id, user_id=user.id, role=role,
        content=content, channel="whatsapp", created_at=at,
    )


async def test_every_new_whatsapp_message_reuses_the_one_conversation(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    user = await _customer(db_session, "+917000000001")
    first = await get_or_create_whatsapp_conversation(db_session, ORG_ID, user.id)
    await db_session.commit()

    # Hours, days later, even after the old code would have "ended" it.
    first.ended_at = T0
    await db_session.commit()
    again = await get_or_create_whatsapp_conversation(db_session, ORG_ID, user.id)
    await db_session.commit()

    assert again.id == first.id
    assert again.ended_at is None
    count = (
        await db_session.execute(
            select(func.count(Conversation.id)).where(Conversation.user_id == user.id)
        )
    ).scalar_one()
    assert count == 1


async def test_other_customers_and_voice_calls_get_their_own_rows(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    a = await _customer(db_session, "+917000000002")
    b = await _customer(db_session, "+917000000003")
    call = Conversation(org_id=ORG_ID, user_id=a.id, channel="voice", started_at=T0)
    db_session.add(call)
    await db_session.commit()

    conv_a = await get_or_create_whatsapp_conversation(db_session, ORG_ID, a.id)
    conv_b = await get_or_create_whatsapp_conversation(db_session, ORG_ID, b.id)
    assert conv_a.id != call.id  # a voice row is never reused for chat
    assert conv_a.id != conv_b.id


async def test_merge_moves_everything_and_keeps_voice(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    user = await _customer(db_session, "+917000000004")
    mon = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0,
                       tags=["hot"], summary="Monday: asked price")
    tue = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp",
                       started_at=T0 + timedelta(days=1), tags=["hot", "demo"], summary="Tuesday: booked")
    wed = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp",
                       started_at=T0 + timedelta(days=2), ended_at=T0 + timedelta(days=2, hours=1))
    call = Conversation(org_id=ORG_ID, user_id=user.id, channel="voice",
                        started_at=T0 + timedelta(days=1, hours=3), recording_url="https://rec/1.mp3")
    db_session.add_all([mon, tue, wed, call])
    await db_session.flush()
    campaign = CallCampaign(org_id=ORG_ID, name="c", criteria="any", channel="whatsapp")
    db_session.add(campaign)
    await db_session.flush()
    target = CampaignTarget(
        org_id=ORG_ID, campaign_id=campaign.id, phone=user.phone, conversation_id=wed.id
    )
    lead = Lead(org_id=ORG_ID, user_id=user.id, conversation_id=tue.id)
    turn = ConversationTurn(
        org_id=ORG_ID, conversation_id=tue.id, user_id=user.id, channel="whatsapp", user_text="hi", state="greeting"
    )
    db_session.add_all([target, lead, turn])
    db_session.add_all(
        [
            _msg(user, mon, "m1", T0), _msg(user, mon, "m2", T0 + timedelta(minutes=1)),
            _msg(user, tue, "t1", T0 + timedelta(days=1)),
            _msg(user, wed, "w1", T0 + timedelta(days=2)),
            Message(org_id=ORG_ID, conversation_id=call.id, user_id=user.id, role="user",
                    content="on call", channel="voice", created_at=T0 + timedelta(days=1, hours=3)),
        ]
    )
    await db_session.commit()

    assert await find_duplicate_groups(db_session) == [(ORG_ID, user.id, 3)]
    result = await merge_customer(db_session, ORG_ID, user.id)
    await db_session.commit()

    assert result is not None and result.survivor_id == mon.id
    assert result.merged_conversations == 2 and result.messages_moved == 2

    rows = (
        await db_session.execute(select(Conversation).where(Conversation.user_id == user.id))
    ).scalars().all()
    assert sorted(c.channel for c in rows) == ["voice", "whatsapp"]
    survivor = next(c for c in rows if c.channel == "whatsapp")
    assert survivor.id == mon.id and survivor.ended_at is None
    assert survivor.tags == ["hot", "demo"]
    assert survivor.summary == "Monday: asked price\n\nTuesday: booked"

    wa_messages = (
        await db_session.execute(
            select(Message.content).where(Message.conversation_id == mon.id).order_by(Message.created_at)
        )
    ).scalars().all()
    assert wa_messages == ["m1", "m2", "t1", "w1"]
    # Call row and its transcript are untouched.
    call_row = next(c for c in rows if c.channel == "voice")
    assert call_row.recording_url == "https://rec/1.mp3"
    assert (
        await db_session.execute(select(func.count(Message.id)).where(Message.conversation_id == call.id))
    ).scalar_one() == 1
    # Everything that pointed at a merged row now points at the survivor.
    await db_session.refresh(target)
    await db_session.refresh(lead)
    await db_session.refresh(turn)
    assert target.conversation_id == lead.conversation_id == turn.conversation_id == mon.id

    # Idempotent.
    assert await find_duplicate_groups(db_session) == []
    assert await merge_customer(db_session, ORG_ID, user.id) is None


async def test_engine_state_and_config_reset_after_idle_gap(db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Test Org"))
    user = await _customer(db_session, "+917000000005")
    conv = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0)
    db_session.add(conv)
    await db_session.flush()
    old = datetime.now(UTC) - timedelta(days=3)
    db_session.add(
        ConversationTurn(
            org_id=ORG_ID, conversation_id=conv.id, user_id=user.id,
            channel="whatsapp", user_text="bye", state="closing", created_at=old,
        )
    )
    await db_session.commit()

    # Voice-style call (no bound) still sees the turn; a WhatsApp session that
    # has been idle for days starts fresh.
    assert await _last_state(db_session, ORG_ID, conv.id) == "closing"
    assert await _last_state(db_session, ORG_ID, conv.id, since=session_since()) is None

    # A turn inside the window continues the session.
    db_session.add(
        ConversationTurn(
            org_id=ORG_ID, conversation_id=conv.id, user_id=user.id,
            channel="whatsapp", user_text="ok", state="qualifying",
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    await db_session.commit()
    assert await _last_state(db_session, ORG_ID, conv.id, since=session_since()) == "qualifying"

    # resolve_config with a bound must not raise and falls back to the active config.
    config = await resolve_config(db_session, ORG_ID, conv.id, since=session_since())
    assert config is not None


async def test_chat_list_includes_leads_with_no_conversation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.config import settings

    await _seed_org(db_session)
    imported = await _customer(db_session, "+917000000006")
    db_session.add(Lead(org_id=ORG_ID, user_id=imported.id, name="Imported Ira"))
    stranger = await _customer(db_session, "+917000000007")  # no lead, no chat: stays out
    await db_session.commit()

    headers = {"X-Admin-Token": settings.admin_token}
    rows = (await client.get("/admin/chat/leads", headers=headers)).json()
    assert [r["phone"] for r in rows] == ["+917000000006"]
    assert rows[0]["last_message_preview"] is None and rows[0]["conversation_count"] == 0
    assert str(stranger.id) not in {r["user_id"] for r in rows}

    # A channel filter only returns people who actually used that channel.
    assert (await client.get("/admin/chat/leads?channel=voice", headers=headers)).json() == []
    # And their (empty) thread still opens.
    thread = await client.get(f"/admin/chat/leads/{imported.id}", headers=headers)
    assert thread.status_code == 200 and thread.json()["messages"] == []
    _ = uuid
