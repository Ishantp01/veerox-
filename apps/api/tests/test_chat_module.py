"""Unified Chat module (routers/chat.py): one thread per customer stitched from
every conversation row, with date-ordered messages, unread markers and the same
member scoping as the rest of the dashboard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import Conversation, Lead, Message, User
from apps.api.routers import chat as chat_router
from apps.api.tests.test_admin_endpoints import (  # noqa: F401  (fixtures + helpers)
    ORG_ID,
    _login_as,
    _member_and_id,
    _require_session_auth,
    _seed_org,
    client,
    fake_redis,
)

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def _msg(user: User, conv: Conversation, role: str, content: str, at: datetime, channel: str):
    return Message(
        org_id=ORG_ID,
        conversation_id=conv.id,
        user_id=user.id,
        role=role,
        content=content,
        channel=channel,
        created_at=at,
    )


@pytest.fixture(autouse=True)
def _old_unread_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_router, "CHAT_UNREAD_EPOCH", datetime(2000, 1, 1, tzinfo=UTC))


async def _seed_customer(db: AsyncSession, phone: str, name: str | None = None) -> User:
    user = User(org_id=ORG_ID, phone=phone, name=name)
    db.add(user)
    await db.flush()
    return user


async def test_thread_merges_every_conversation_in_date_order(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = await _seed_customer(db_session, "+911111111111", "Asha")
    # Three separate rows for one customer: Monday WhatsApp, Tuesday call,
    # Tuesday WhatsApp. Inserted out of order on purpose.
    tue_wa = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0 + timedelta(days=1, hours=5))
    mon_wa = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0)
    tue_call = Conversation(
        org_id=ORG_ID, user_id=user.id, channel="voice", started_at=T0 + timedelta(days=1),
        recording_url="https://rec/1.mp3", recording_duration_secs=42.0, summary="Asked pricing",
    )
    db_session.add_all([tue_wa, mon_wa, tue_call])
    await db_session.flush()
    db_session.add_all(
        [
            _msg(user, tue_wa, "assistant", "tue wa reply", T0 + timedelta(days=1, hours=5, minutes=1), "whatsapp"),
            _msg(user, mon_wa, "user", "mon hello", T0, "whatsapp"),
            _msg(user, tue_call, "user", "call line", T0 + timedelta(days=1, minutes=1), "voice"),
            _msg(user, mon_wa, "assistant", "mon reply", T0 + timedelta(minutes=1), "whatsapp"),
            _msg(user, mon_wa, "system", "hidden plumbing", T0 + timedelta(minutes=2), "whatsapp"),
        ]
    )
    db_session.add(Lead(org_id=ORG_ID, user_id=user.id, name="Asha"))
    await db_session.commit()

    listing = await client.get("/admin/chat/leads", headers={"X-Admin-Token": _admin_token()})
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1  # one entry per lead, not per conversation row
    assert rows[0]["conversation_count"] == 3
    assert rows[0]["last_message_preview"] == "tue wa reply"

    thread = await client.get(
        f"/admin/chat/leads/{user.id}", headers={"X-Admin-Token": _admin_token()}
    )
    assert thread.status_code == 200
    body = thread.json()
    assert [m["content"] for m in body["messages"]] == [
        "mon hello", "mon reply", "call line", "tue wa reply",
    ]
    assert {c["channel"] for c in body["conversations"]} == {"whatsapp", "voice"}
    call = next(c for c in body["conversations"] if c["channel"] == "voice")
    assert call["recording_url"] == "https://rec/1.mp3" and call["summary"] == "Asked pricing"
    assert body["contact"]["phone"] == "+911111111111"


async def test_unread_count_and_mark_read(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    user = await _seed_customer(db_session, "+912222222222")
    conv = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp", started_at=T0)
    db_session.add(conv)
    await db_session.flush()
    db_session.add_all(
        [
            _msg(user, conv, "user", "one", T0, "whatsapp"),
            _msg(user, conv, "assistant", "bot", T0 + timedelta(minutes=1), "whatsapp"),
            _msg(user, conv, "user", "two", T0 + timedelta(minutes=2), "whatsapp"),
        ]
    )
    await db_session.commit()

    with _require_session_auth(True):
        headers = await _login_as(client, db_session, email="chat-admin@example.com", role="admin")
        first = await client.get("/admin/chat/leads", headers=headers)
        assert first.json()[0]["unread_count"] == 2  # only inbound counts

        marked = await client.post(f"/admin/chat/leads/{user.id}/read", headers=headers)
        assert marked.status_code == 204

        after = await client.get("/admin/chat/leads", headers=headers)
        assert after.json()[0]["unread_count"] == 0

        # A later inbound message makes it unread again.
        db_session.add(
            _msg(user, conv, "user", "three", datetime.now(UTC) + timedelta(seconds=5), "whatsapp")
        )
        await db_session.commit()
        again = await client.get("/admin/chat/leads", headers=headers)
        assert again.json()[0]["unread_count"] == 1


async def test_channel_filter_and_search(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    wa_user = await _seed_customer(db_session, "+913333333333", "Wally")
    voice_user = await _seed_customer(db_session, "+914444444444", "Vicky")
    db_session.add_all(
        [
            Conversation(org_id=ORG_ID, user_id=wa_user.id, channel="whatsapp", started_at=T0),
            Conversation(org_id=ORG_ID, user_id=voice_user.id, channel="voice", started_at=T0),
        ]
    )
    await db_session.commit()
    headers = {"X-Admin-Token": _admin_token()}

    voice_only = (await client.get("/admin/chat/leads?channel=voice", headers=headers)).json()
    assert [r["name"] for r in voice_only] == ["Vicky"]

    found = (await client.get("/admin/chat/leads?search=3333", headers=headers)).json()
    assert [r["name"] for r in found] == ["Wally"]


async def test_member_only_sees_claimed_customers(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    mine = await _seed_customer(db_session, "+915555555555", "Mine")
    theirs = await _seed_customer(db_session, "+916666666666", "Theirs")
    member_headers, member_id = await _member_and_id(client, db_session, "chat-member@example.com")
    db_session.add_all(
        [
            Lead(org_id=ORG_ID, user_id=mine.id, claimed_by_account_user_id=member_id),
            Lead(org_id=ORG_ID, user_id=theirs.id),
            Conversation(org_id=ORG_ID, user_id=mine.id, channel="whatsapp", started_at=T0),
            Conversation(org_id=ORG_ID, user_id=theirs.id, channel="whatsapp", started_at=T0),
        ]
    )
    await db_session.commit()

    with _require_session_auth(True):
        listing = await client.get("/admin/chat/leads", headers=member_headers)
        assert [r["name"] for r in listing.json()] == ["Mine"]
        assert (await client.get(f"/admin/chat/leads/{mine.id}", headers=member_headers)).status_code == 200
        assert (await client.get(f"/admin/chat/leads/{theirs.id}", headers=member_headers)).status_code == 404
        assert (
            await client.post(f"/admin/chat/leads/{theirs.id}/read", headers=member_headers)
        ).status_code == 404


async def test_chat_only_shows_the_org_you_are_in(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    other_org = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    await _seed_org(db_session)
    from apps.api.db.models import Org

    db_session.add(Org(id=other_org, name="Other Org"))
    await db_session.flush()
    mine = await _seed_customer(db_session, "+919179609988", "Mine")
    theirs = User(org_id=other_org, phone="+919179609988", name="Same number, other org")
    db_session.add(theirs)
    await db_session.flush()
    for u, org in ((mine, ORG_ID), (theirs, other_org)):
        db_session.add(Conversation(org_id=org, user_id=u.id, channel="whatsapp", started_at=T0))
    await db_session.commit()

    headers = {"X-Admin-Token": _admin_token()}
    rows = (await client.get("/admin/chat/leads", headers=headers)).json()
    assert [r["name"] for r in rows] == ["Mine"]  # never the other org's copy
    assert (await client.get(f"/admin/chat/leads/{theirs.id}", headers=headers)).status_code == 404


async def test_unknown_customer_is_404(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    response = await client.get(
        f"/admin/chat/leads/{uuid.uuid4()}", headers={"X-Admin-Token": _admin_token()}
    )
    assert response.status_code == 404


def _admin_token() -> str:
    from apps.api.config import settings

    return settings.admin_token
