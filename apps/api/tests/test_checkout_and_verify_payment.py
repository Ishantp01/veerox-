"""Covers POST /billing/checkout-session (free-plan instant activation vs.
paid-plan Razorpay Order creation) and POST /billing/verify-payment — the
no-webhook payment confirmation path used for local/test-mode development
(see apps/api/routers/billing.py's verify_payment docstring).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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


async def test_verify_payment_full_plan_resets_all_resources(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Locks in the unchanged existing behavior for resource_type=None: a
    full-plan payment still replaces plan_id, resets plan_started_at, and
    now also snapshots the plan's limits into org.resource_limits."""
    headers = await _seed_org_and_login(client, db_session)
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents=490000,
        limits={
            "max_call_minutes": 2000,
            "max_whatsapp_messages": 5000,
            "max_seats": 10,
            "max_campaigns": 20,
        },
    )
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_full",
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
                "razorpay_payment_id": "pay_full",
                "razorpay_order_id": "order_full",
                "razorpay_signature": "fakesig",
            },
            headers=headers,
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.plan_id == plan.id
    assert org.plan_started_at is not None
    assert org.resource_limits == plan.limits


async def test_verify_payment_recharge_only_bumps_targeted_resource(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The core Scenario 1 case: a Call-Minutes-only recharge must add to
    call minutes and leave WhatsApp messages, team members, and campaigns
    completely untouched from what the org's base plan already granted."""
    headers = await _seed_org_and_login(client, db_session)
    base_plan = Plan(
        code="pro",
        name="Pro",
        price_cents=490000,
        limits={
            "max_call_minutes": 500,
            "max_whatsapp_messages": 3000,
            "max_seats": 5,
            "max_campaigns": 10,
        },
    )
    db_session.add(base_plan)
    await db_session.flush()
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    org.plan_id = base_plan.id
    org.plan_started_at = datetime.now(UTC)
    pre_recharge_plan_started_at = org.plan_started_at

    recharge_plan = Plan(
        code="call-minutes-1000",
        name="1000 Call Minutes",
        price_cents=99900,
        limits={"max_call_minutes": 1000},
        resource_type="max_call_minutes",
    )
    db_session.add(recharge_plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_recharge_call",
            plan_id=recharge_plan.id,
            amount_cents=99900,
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
                "razorpay_payment_id": "pay_recharge_call",
                "razorpay_order_id": "order_recharge_call",
                "razorpay_signature": "fakesig",
            },
            headers=headers,
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    # Only call minutes moved — 500 (base plan) + 1000 (recharge) = 1500.
    assert org.resource_limits == {
        "max_call_minutes": 1500,
        "max_whatsapp_messages": 3000,
        "max_seats": 5,
        "max_campaigns": 10,
    }
    # plan_id and plan_started_at are untouched — moving plan_started_at
    # would reset the usage window for every resource, not just this one.
    assert org.plan_id == base_plan.id
    assert org.plan_started_at == pre_recharge_plan_started_at


async def test_verify_payment_whatsapp_recharge_does_not_affect_call_minutes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _seed_org_and_login(client, db_session)
    base_plan = Plan(
        code="pro",
        name="Pro",
        price_cents=490000,
        limits={
            "max_call_minutes": 1500,
            "max_whatsapp_messages": 3000,
            "max_seats": 5,
            "max_campaigns": 10,
        },
    )
    db_session.add(base_plan)
    await db_session.flush()
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    org.plan_id = base_plan.id
    await db_session.commit()

    recharge_plan = Plan(
        code="whatsapp-5000",
        name="5000 WhatsApp Messages",
        price_cents=149900,
        limits={"max_whatsapp_messages": 5000},
        resource_type="max_whatsapp_messages",
    )
    db_session.add(recharge_plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_recharge_whatsapp",
            plan_id=recharge_plan.id,
            amount_cents=149900,
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
                "razorpay_payment_id": "pay_recharge_whatsapp",
                "razorpay_order_id": "order_recharge_whatsapp",
                "razorpay_signature": "fakesig",
            },
            headers=headers,
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.resource_limits == {
        "max_call_minutes": 1500,
        "max_whatsapp_messages": 8000,
        "max_seats": 5,
        "max_campaigns": 10,
    }


async def test_verify_payment_recharge_for_metric_absent_on_current_plan(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The org's current plan never included WhatsApp at all (key absent).
    Recharging WhatsApp should land at exactly the recharged amount, not
    error or silently no-op."""
    headers = await _seed_org_and_login(client, db_session)
    base_plan = Plan(
        code="call-only",
        name="Call Only",
        price_cents=490000,
        limits={"max_call_minutes": 1000},
    )
    db_session.add(base_plan)
    await db_session.flush()
    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    org.plan_id = base_plan.id
    await db_session.commit()

    recharge_plan = Plan(
        code="whatsapp-2000",
        name="2000 WhatsApp Messages",
        price_cents=99900,
        limits={"max_whatsapp_messages": 2000},
        resource_type="max_whatsapp_messages",
    )
    db_session.add(recharge_plan)
    await db_session.flush()
    db_session.add(
        BillingPayment(
            org_id=ORG_ID,
            provider="razorpay",
            provider_order_id="order_recharge_new_channel",
            plan_id=recharge_plan.id,
            amount_cents=99900,
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
                "razorpay_payment_id": "pay_recharge_new_channel",
                "razorpay_order_id": "order_recharge_new_channel",
                "razorpay_signature": "fakesig",
            },
            headers=headers,
        )
    assert response.status_code == 204

    org = (await db_session.execute(select(Org).where(Org.id == ORG_ID))).scalar_one()
    assert org.resource_limits["max_whatsapp_messages"] == 2000
    assert org.resource_limits["max_call_minutes"] == 1000


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
