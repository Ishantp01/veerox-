"""Covers apps/api/core/usage.py's monthly aggregation and the plan-limit
enforcement wired into /admin/outbound/whatsapp and /admin/outbound/call,
plus GET /billing/usage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.core.usage import get_monthly_usage
from apps.api.db.models import (
    AccountUser,
    Conversation,
    Message,
    Org,
    OrgMembership,
    Plan,
    User,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed_org(db: AsyncSession) -> Org:
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.commit()
    return org


async def _seed_whatsapp_messages(db: AsyncSession, count: int) -> None:
    user = User(org_id=ORG_ID, phone="+910000000099")
    db.add(user)
    await db.flush()
    conversation = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp")
    db.add(conversation)
    await db.flush()
    for _ in range(count):
        db.add(
            Message(
                org_id=ORG_ID,
                conversation_id=conversation.id,
                user_id=user.id,
                role="assistant",
                content="hi",
                channel="whatsapp",
                created_at=datetime.now(UTC),
            )
        )
    await db.commit()


async def test_get_monthly_usage_aggregates_call_minutes(db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+910000000098")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(
        org_id=ORG_ID, user_id=user.id, channel="voice", recording_duration_secs=120.0
    )
    db_session.add(conversation)
    await db_session.commit()

    usage = await get_monthly_usage(db_session, ORG_ID)
    assert usage.call_minutes == 2.0


async def test_billing_usage_endpoint_reflects_plan_limits(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_org(db_session)
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents_monthly=490000,
        limits={"max_whatsapp_messages_per_month": 5},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id

    login_token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role="admin"))
    await db_session.commit()

    await _seed_whatsapp_messages(db_session, 3)

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    response = await client.get("/billing/usage", headers={"X-Session-Token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["whatsapp_messages"]["used"] == 3
    assert body["whatsapp_messages"]["limit"] == 5


async def test_outbound_whatsapp_blocked_once_over_plan_limit(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_org(db_session)
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents_monthly=490000,
        limits={"max_whatsapp_messages_per_month": 2},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id
    await db_session.commit()

    await _seed_whatsapp_messages(db_session, 2)

    response = await client.post(
        "/admin/outbound/whatsapp",
        json={"phone": "+910000000097", "text": "hello"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 402


async def test_outbound_whatsapp_blocked_when_past_due(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_org(db_session)
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents_monthly=490000,
        limits={"max_whatsapp_messages_per_month": 1000},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id
    org.billing_status = "past_due"
    await db_session.commit()

    response = await client.post(
        "/admin/outbound/whatsapp",
        json={"phone": "+910000000095", "text": "hello"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 402


async def test_outbound_whatsapp_allowed_under_plan_limit(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keep the real send in local-dev stub mode rather than hitting Meta's
    # live Graph API with whatever real token happens to be in .env — same
    # convention as test_admin_endpoints.py's outbound WhatsApp tests.
    monkeypatch.setattr(settings, "meta_access_token", None)

    org = await _seed_org(db_session)
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents_monthly=490000,
        limits={"max_whatsapp_messages_per_month": 10},
    )
    db_session.add(plan)
    await db_session.flush()
    org.plan_id = plan.id
    await db_session.commit()

    await _seed_whatsapp_messages(db_session, 2)

    response = await client.post(
        "/admin/outbound/whatsapp",
        json={"phone": "+910000000096", "text": "hello"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
