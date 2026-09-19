"""Tests for apps.api.routers.lead_follow_ups — the flattened per-lead
follow-up slots feeding the Follow-up Tasks page and its due popup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Lead, Org, User

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.flush()
    user = User(org_id=ORG_ID, phone="+910000000201")
    db.add(user)
    await db.flush()
    now = datetime.now(UTC)
    db.add(
        Lead(
            org_id=ORG_ID,
            user_id=user.id,
            name="Rahul",
            phone="+910000000201",
            channel="voice",
            status="contacted",
            follow_up_at=now - timedelta(hours=1),
            follow_up_note="call back",
            follow_up_3_at=now + timedelta(days=2),
            follow_up_3_note="send quote",
        )
    )
    await db.commit()


async def test_lists_each_scheduled_slot_soonest_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.get("/lead-follow-ups", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["slot"] for r in rows] == [1, 3]
    assert rows[0]["note"] == "call back"
    assert rows[0]["status"] == "contacted"


async def test_due_only_returns_follow_ups_already_reached(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.get("/lead-follow-ups?due=true", headers=ADMIN_HEADERS)
    assert [r["slot"] for r in resp.json()] == [1]


async def test_search_matches_note(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed(db_session)
    resp = await client.get("/lead-follow-ups?search=quote", headers=ADMIN_HEADERS)
    assert [r["slot"] for r in resp.json()] == [3]
