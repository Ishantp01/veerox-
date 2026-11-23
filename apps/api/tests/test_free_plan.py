"""Free (price 0) plans: created by the platform owner, never bought, granted to ONE org with no payment, and then
limited by the normal plan limits. Orgs that were not granted keep behaving exactly as before."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.plan_limits import enforce_plan_limit, get_active_plan
from apps.api.db.models import Org
from apps.api.db.models.razorpay_payment import RazorpayPayment

ADMIN = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID(settings.default_org_id)


async def _free_plan(client: AsyncClient, **limits: int | None) -> str:
    resp = await client.post(
        "/payments/admin/plans",
        headers=ADMIN,
        json={"code": "free", "name": "Free", "price_paise": 0, "duration_days": 30, **limits},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_free_plan_can_be_created_but_a_negative_price_cannot(client: AsyncClient) -> None:
    await _free_plan(client, max_whatsapp_messages=100)
    bad = await client.post(
        "/payments/admin/plans",
        headers=ADMIN,
        json={"code": "neg", "name": "Neg", "price_paise": -1, "duration_days": 30},
    )
    assert bad.status_code == 422


async def test_free_plan_cannot_be_checked_out(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    plan_id = await _free_plan(client)
    resp = await client.post("/payments/checkout", headers=ADMIN, json={"plan_id": plan_id})
    assert resp.status_code == 400


async def test_grant_gives_the_org_the_plan_limits(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    plan_id = await _free_plan(client, max_whatsapp_messages=100)

    # Before the grant: no plan, so no plan limit (unchanged behaviour).
    assert await get_active_plan(db_session, ORG_ID) is None

    resp = await client.post(
        "/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": plan_id}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["amount_paise"] == 0 and body["status"] == "paid"

    active = await get_active_plan(db_session, ORG_ID)
    assert active is not None and active.plan.code == "free"
    await enforce_plan_limit(db_session, ORG_ID, "whatsapp_messages")  # 0 of 100 used: allowed


async def test_zero_message_free_plan_blocks_sending(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    plan_id = await _free_plan(client, max_whatsapp_messages=0)
    resp = await client.post(
        "/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": plan_id}
    )
    assert resp.status_code == 201
    with pytest.raises(HTTPException) as err:
        await enforce_plan_limit(db_session, ORG_ID, "whatsapp_messages")
    assert err.value.status_code in (402, 403, 429)


async def test_grant_is_refused_for_paid_plans_unknown_orgs_and_duplicates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    free_id = await _free_plan(client)
    paid = await client.post(
        "/payments/admin/plans",
        headers=ADMIN,
        json={"code": "basic", "name": "Basic", "price_paise": 99900, "duration_days": 30},
    )
    paid_id = paid.json()["id"]

    # A paid plan can never be granted for free.
    r = await client.post("/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": paid_id})
    assert r.status_code == 400
    # Unknown org.
    r = await client.post(
        "/payments/admin/grant", headers=ADMIN, json={"org_id": str(uuid.uuid4()), "plan_id": free_id}
    )
    assert r.status_code == 404
    # Granted once; a second grant while it is active is refused.
    assert (
        await client.post("/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": free_id})
    ).status_code == 201
    assert (
        await client.post("/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": free_id})
    ).status_code == 409


async def test_grant_requires_the_platform_admin_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/payments/admin/grant", json={"org_id": str(uuid.uuid4()), "plan_id": str(uuid.uuid4())}
    )
    assert resp.status_code in (401, 403)


async def test_free_plan_is_hidden_from_the_shop(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    await _free_plan(client)
    resp = await client.get("/payments/plans", headers=ADMIN)
    assert resp.status_code == 200
    assert all(p["price_paise"] > 0 for p in resp.json())
    assert not [p for p in resp.json() if p["code"] == "free"]
    assert RazorpayPayment is not None  # model import kept for readability of the grant tests


async def test_end_plan_closes_the_period_keeps_history_and_lets_a_free_plan_be_granted(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from apps.api.db.models.razorpay_payment import PaymentPlan

    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.flush()
    basic = PaymentPlan(code="basic", name="Basic", price_paise=100, duration_days=30, max_whatsapp_messages=2)
    db_session.add(basic)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(
        RazorpayPayment(
            org_id=ORG_ID, plan_id=basic.id, razorpay_order_id="order_basic", amount_paise=100, status="paid",
            period_start=now - timedelta(days=1), period_end=now + timedelta(days=29),
        )
    )
    await db_session.commit()
    free_id = await _free_plan(client, max_whatsapp_messages=1000)

    # While the paid plan runs, a free grant is refused (the case seen in prod).
    blocked = await client.post("/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": free_id})
    assert blocked.status_code == 409

    ended = await client.post("/payments/admin/end-plan", headers=ADMIN, json={"org_id": str(ORG_ID)})
    assert ended.status_code == 200 and ended.json() == {"ended": ["basic"]}
    await db_session.rollback()  # see the committed change from the request's session
    assert await get_active_plan(db_session, ORG_ID) is None
    kept = (await db_session.execute(select(RazorpayPayment).where(RazorpayPayment.razorpay_order_id == "order_basic"))).scalar_one()
    assert kept.amount_paise == 100 and kept.status == "paid"  # history intact, only the period was closed

    granted = await client.post("/payments/admin/grant", headers=ADMIN, json={"org_id": str(ORG_ID), "plan_id": free_id})
    assert granted.status_code == 201
    active = await get_active_plan(db_session, ORG_ID)
    assert active is not None and active.plan.code == "free"


async def test_end_plan_refuses_unknown_orgs_orgs_without_a_plan_and_missing_token(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    assert (await client.post("/payments/admin/end-plan", headers=ADMIN, json={"org_id": str(uuid.uuid4())})).status_code == 404
    assert (await client.post("/payments/admin/end-plan", headers=ADMIN, json={"org_id": str(ORG_ID)})).status_code == 409
    assert (await client.post("/payments/admin/end-plan", json={"org_id": str(ORG_ID)})).status_code in (401, 403)
