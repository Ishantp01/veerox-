"""Covers POST /billing/checkout-session (free-plan instant activation vs.
paid-plan Razorpay Order creation) and POST /billing/verify-payment — the
no-webhook payment confirmation path used for local/test-mode development
(see apps/api/routers/billing.py's verify_payment docstring).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, BillingPayment, Org, OrgMembership, Plan

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _seed_org_and_login(client: AsyncClient, db: AsyncSession) -> dict[str, str]:
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.flush()
    login_token = generate_login_token()
    account = AccountUser(email="admin@example.com", token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    return {"X-Session-Token": login.json()["token"]}


async def _seed_org_and_login_as_member(client: AsyncClient, db: AsyncSession) -> dict[str, str]:
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.flush()
    login_token = generate_login_token()
    account = AccountUser(email="member@example.com", token_hash=hash_token(login_token))
    db.add(account)
    await db.flush()
    db.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="member"))
    await db.commit()

    login = await client.post("/auth/login", json={"token": login_token})
    return {"X-Session-Token": login.json()["token"]}


async def test_checkout_session_rejects_member_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login_as_member(client, db_session)
    plan = Plan(code="basic", name="Basic", price_cents=0, limits={})
    db_session.add(plan)
    await db_session.commit()

    response = await client.post(
        "/billing/checkout-session",
        json={
            "plan_code": "basic",
            "success_url": "http://localhost:3001/billing?checkout=success",
            "cancel_url": "http://localhost:3001/billing?checkout=cancel",
        },
        headers=headers,
    )
    assert response.status_code == 403


async def test_checkout_session_activates_free_plan_instantly(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    plan = Plan(code="basic", name="Basic", price_cents=0, limits={})
    db_session.add(plan)
    await db_session.commit()

    response = await client.post(
        "/billing/checkout-session",
        json={
            "plan_code": "basic",
            "success_url": "http://localhost:3001/billing?checkout=success",
            "cancel_url": "http://localhost:3001/billing?checkout=cancel",
        },
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["checkout_url"] is not None
    assert response.json()["order_id"] is None

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.plan_id == plan.id
    assert org.billing_status == "active"


async def test_checkout_session_paid_plan_returns_order_for_embedded_checkout(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    plan = Plan(code="pro", name="Pro", price_cents=490000, limits={})
    db_session.add(plan)
    await db_session.commit()

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.razorpay.Client") as mock_client_cls:
        mock_client_cls.return_value.order.create.return_value = {
            "id": "order_abc123",
            "status": "created",
        }
        response = await client.post(
            "/billing/checkout-session",
            json={
                "plan_code": "pro",
                "success_url": "http://localhost:3001/billing?checkout=success",
                "cancel_url": "http://localhost:3001/billing?checkout=cancel",
            },
            headers=headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["checkout_url"] is None
    assert body["order_id"] == "order_abc123"
    assert body["amount_cents"] == 490000
    assert body["razorpay_key_id"] == "rzp_test_x"

    # Org isn't activated yet — only verify-payment (or the webhook) does that.
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.plan_id is None


async def test_verify_payment_activates_org_on_valid_signature(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    plan = Plan(code="pro", name="Pro", price_cents=490000, limits={})
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_abc123",
            plan_id=plan.id,
            amount_cents=490000,
            status="created",
        )
    )
    await db_session.commit()

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.razorpay.Client") as mock_client_cls:
        mock_client_cls.return_value.utility.verify_payment_signature.return_value = True
        response = await client.post(
            "/billing/verify-payment",
            json={
                "razorpay_payment_id": "pay_xyz",
                "razorpay_order_id": "order_abc123",
                "razorpay_signature": "fakesig",
            },
            headers=headers,
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.plan_id == plan.id
    assert org.billing_status == "active"

    payment = (
        await db_session.execute(
            select(BillingPayment).where(BillingPayment.provider_order_id == "order_abc123")
        )
    ).scalar_one()
    assert payment.status == "paid"
    assert payment.provider_payment_id == "pay_xyz"
    # A recharge stamps when it landed but has no end date — credits expire
    # by consumption, not by a timer (see routers/billing.py).
    assert payment.period_start is not None
    assert payment.period_end is None
    # ...and it resets the org's credit window, which is what actually
    # restores usage headroom (core/usage.py counts from plan_started_at).
    assert org.plan_started_at is not None


async def test_verify_payment_rejects_bad_signature(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    plan = Plan(code="pro", name="Pro", price_cents=490000, limits={})
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_bad",
            plan_id=plan.id,
            amount_cents=490000,
            status="created",
        )
    )
    await db_session.commit()

    from razorpay.errors import SignatureVerificationError

    with patch("apps.api.routers.billing.settings.razorpay_key_id", "rzp_test_x"), patch(
        "apps.api.routers.billing.settings.razorpay_key_secret", "secret_x"
    ), patch("apps.api.routers.billing.razorpay.Client") as mock_client_cls:
        mock_client_cls.return_value.utility.verify_payment_signature.side_effect = (
            SignatureVerificationError("bad signature")
        )
        response = await client.post(
            "/billing/verify-payment",
            json={
                "razorpay_payment_id": "pay_xyz",
                "razorpay_order_id": "order_bad",
                "razorpay_signature": "wrongsig",
            },
            headers=headers,
        )
    assert response.status_code == 400

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.plan_id is None


async def test_available_plans_reflects_admin_catalog_not_hardcoded(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    db_session.add(
        Plan(code="basic", name="Basic", price_cents=0, limits={}, is_active=True)
    )
    db_session.add(
        Plan(
            code="enterprise",
            name="Enterprise",
            price_cents=9990000,
            limits={},
            is_active=True,
        )
    )
    db_session.add(
        Plan(code="retired", name="Retired", price_cents=100, limits={}, is_active=False)
    )
    await db_session.commit()

    response = await client.get("/billing/available-plans", headers=headers)
    assert response.status_code == 200
    codes = {p["code"] for p in response.json()}
    assert codes == {"basic", "enterprise"}
