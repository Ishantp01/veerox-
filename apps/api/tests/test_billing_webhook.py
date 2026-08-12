from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models import BillingEvent, BillingPayment, Org, Plan

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _fake_event(event_type: str, payment: dict | None = None) -> dict:
    payload = {}
    if payment is not None:
        payload["payment"] = {"entity": payment}
    return {"entity": "event", "event": event_type, "payload": payload, "created_at": 1690000000}


async def _seed_org_and_plan(db: AsyncSession) -> Plan:
    plan = Plan(code="pro", name="Pro", price_cents_monthly=490000, limits={"max_seats": 5})
    db.add(plan)
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.commit()
    return plan


async def test_webhook_payment_captured_activates_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    plan = await _seed_org_and_plan(db_session)
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_123",
            plan_id=plan.id,
            amount_cents=490000,
            status="created",
        )
    )
    await db_session.commit()

    event = _fake_event(
        "payment.captured",
        payment={"id": "pay_123", "order_id": "order_123"},
    )

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.settings.razorpay_webhook_secret", "whsec_test"), patch(
        "apps.api.routers.billing.razorpay.Client"
    ) as mock_client_cls:
        mock_client_cls.return_value.utility.verify_webhook_signature.return_value = True
        response = await client.post(
            "/billing/webhook",
            json=event,
            headers={"X-Razorpay-Signature": "fake", "X-Razorpay-Event-Id": "evt_1"},
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.billing_status == "active"
    assert org.plan_id == plan.id

    events = (await db_session.execute(select(BillingEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].provider_event_id == "evt_1"

    payment = (
        await db_session.execute(
            select(BillingPayment).where(BillingPayment.provider_order_id == "order_123")
        )
    ).scalar_one()
    assert payment.status == "paid"


async def test_webhook_is_idempotent_on_duplicate_event_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    plan = await _seed_org_and_plan(db_session)
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_dup",
            plan_id=plan.id,
            amount_cents=490000,
            status="created",
        )
    )
    await db_session.commit()

    event = _fake_event(
        "payment.captured",
        payment={"id": "pay_dup", "order_id": "order_dup"},
    )

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.settings.razorpay_webhook_secret", "whsec_test"), patch(
        "apps.api.routers.billing.razorpay.Client"
    ) as mock_client_cls:
        mock_client_cls.return_value.utility.verify_webhook_signature.return_value = True
        first = await client.post(
            "/billing/webhook",
            json=event,
            headers={"X-Razorpay-Signature": "fake", "X-Razorpay-Event-Id": "evt_dup"},
        )
        second = await client.post(
            "/billing/webhook",
            json=event,
            headers={"X-Razorpay-Signature": "fake", "X-Razorpay-Event-Id": "evt_dup"},
        )

    assert first.status_code == 204
    assert second.status_code == 204
    events = (await db_session.execute(select(BillingEvent))).scalars().all()
    assert len(events) == 1


async def test_webhook_payment_failed_marks_payment_failed_without_downgrading_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    plan = await _seed_org_and_plan(db_session)
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_456",
            plan_id=plan.id,
            amount_cents=490000,
            status="created",
        )
    )
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    org.billing_status = "active"
    await db_session.commit()

    event = _fake_event("payment.failed", payment={"id": "pay_1", "order_id": "order_456"})

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.settings.razorpay_webhook_secret", "whsec_test"), patch(
        "apps.api.routers.billing.razorpay.Client"
    ) as mock_client_cls:
        mock_client_cls.return_value.utility.verify_webhook_signature.return_value = True
        response = await client.post(
            "/billing/webhook",
            json=event,
            headers={"X-Razorpay-Signature": "fake", "X-Razorpay-Event-Id": "evt_fail"},
        )
    assert response.status_code == 204

    # A failed order shouldn't retroactively downgrade an org that's already
    # active from a prior payment — only the pending order itself is marked.
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.billing_status == "active"

    payment = (
        await db_session.execute(
            select(BillingPayment).where(BillingPayment.provider_order_id == "order_456")
        )
    ).scalar_one()
    assert payment.status == "failed"


async def test_billing_status_reflects_plan_and_seats(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.core.security import generate_login_token, hash_token
    from apps.api.db.models import AccountUser, OrgMembership

    plan = await _seed_org_and_plan(db_session)
    login_token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(login_token))
    db_session.add(account)
    await db_session.flush()
    # The owner (invited_by_id is null) doesn't count as a used seat — only
    # teammates actually invited do (matches routers/team.py's max_seats
    # enforcement, which the same figure must agree with).
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role="admin"))

    invited_account = AccountUser(email="teammate@example.com", token_hash=hash_token("x" * 40))
    db_session.add(invited_account)
    await db_session.flush()
    db_session.add(
        OrgMembership(
            org_id=ORG_ID,
            account_user_id=invited_account.id,
            role="agent",
            invited_by_id=account.id,
        )
    )

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    org.plan_id = plan.id
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    token = login.json()["token"]

    response = await client.get("/billing/status", headers={"X-Session-Token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["code"] == "pro"
    assert body["seat_count"] == 1
    assert body["billing_status"] == "trialing"
