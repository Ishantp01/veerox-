"""Razorpay plan payments: checkout -> verify/webhook marks paid and records a
period; org license fields are never touched."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Org
from apps.api.routers import payments

ADMIN = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID(settings.default_org_id)
SECRET = "test_key_secret"
WH_SECRET = "test_webhook_secret"


@pytest.fixture(autouse=True)
def _razorpay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_x")
    monkeypatch.setattr(settings, "razorpay_key_secret", SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", WH_SECRET)

    async def fake_order(amount: int, currency: str, receipt: str) -> dict:
        return {"id": f"order_{uuid.uuid4().hex[:10]}"}

    monkeypatch.setattr(payments, "_create_razorpay_order", fake_order)


async def _setup(client: AsyncClient, db: AsyncSession) -> tuple[Org, str]:
    org = Org(id=ORG_ID, name="Pay Co")
    db.add(org)
    await db.commit()
    resp = await client.post(
        "/payments/admin/plans",
        headers=ADMIN,
        json={"code": "basic", "name": "Basic", "price_paise": 99900, "duration_days": 30},
    )
    assert resp.status_code == 201
    return org, resp.json()["id"]


def _sig(order_id: str, payment_id: str) -> str:
    return hmac.new(
        SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


async def test_checkout_verify_records_period_and_leaves_license(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, plan_id = await _setup(client, db_session)
    before = (org.license_status, org.license_expires_at)

    checkout = await client.post("/payments/checkout", headers=ADMIN, json={"plan_id": plan_id})
    assert checkout.status_code == 200
    order_id = checkout.json()["order_id"]

    bad = await client.post(
        "/payments/verify",
        headers=ADMIN,
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": "x",
        },
    )
    assert bad.status_code == 400

    ok = await client.post(
        "/payments/verify",
        headers=ADMIN,
        json={
            "razorpay_order_id": order_id,
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": _sig(order_id, "pay_1"),
        },
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "paid"

    status = await client.get("/payments/status", headers=ADMIN)
    assert status.json()["active"] is True

    await db_session.refresh(org)
    assert (org.license_status, org.license_expires_at) == before


async def test_webhook_is_signed_and_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    _, plan_id = await _setup(client, db_session)
    order_id = (
        await client.post("/payments/checkout", headers=ADMIN, json={"plan_id": plan_id})
    ).json()["order_id"]
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_9", "order_id": order_id}}},
        }
    ).encode()
    sig = hmac.new(WH_SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert (
        await client.post(
            "/payments/webhook", content=body, headers={"X-Razorpay-Signature": "bad"}
        )
    ).status_code == 400
    for _ in range(2):
        r = await client.post(
            "/payments/webhook", content=body, headers={"X-Razorpay-Signature": sig}
        )
        assert r.status_code == 200

    history = (await client.get("/payments/history", headers=ADMIN)).json()
    assert len(history) == 1 and history[0]["status"] == "paid"


async def test_owner_org_cannot_checkout(client: AsyncClient, db_session: AsyncSession) -> None:
    from apps.api.core.security import generate_login_token, hash_token
    from apps.api.db.models import AccountUser, OrgMembership

    _, plan_id = await _setup(client, db_session)
    account = AccountUser(
        email="owner@veerox.example",
        token_hash=hash_token(generate_login_token()),
        is_superuser=True,
    )
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role="admin"))
    await db_session.commit()

    resp = await client.post("/payments/checkout", headers=ADMIN, json={"plan_id": plan_id})
    assert resp.status_code == 403


# ── plan limits ──────────────────────────────────────────────────────────


async def _paid_plan(db: AsyncSession, **limits: int | None):
    from datetime import UTC, datetime, timedelta

    from apps.api.db.models.razorpay_payment import PaymentPlan, RazorpayPayment

    db.add(Org(id=ORG_ID, name="Limit Co"))
    await db.flush()
    plan = PaymentPlan(code="lim", name="Lim", price_paise=100, duration_days=30, **limits)
    db.add(plan)
    await db.flush()
    now = datetime.now(UTC)
    db.add(
        RazorpayPayment(
            org_id=ORG_ID,
            plan_id=plan.id,
            razorpay_order_id="order_lim",
            amount_paise=100,
            status="paid",
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
        )
    )
    await db.commit()


async def test_no_plan_means_no_limit(db_session: AsyncSession) -> None:
    from apps.api.core.plan_limits import enforce_plan_limit

    db_session.add(Org(id=ORG_ID, name="Free Co"))
    await db_session.commit()
    for r in (
        "call_minutes",
        "whatsapp_messages",
        "team_members",
        "voice_campaigns",
        "whatsapp_campaigns",
    ):
        await enforce_plan_limit(db_session, ORG_ID, r)  # must not raise


async def test_call_minutes_and_campaign_limits_enforced(db_session: AsyncSession) -> None:
    from fastapi import HTTPException

    from apps.api.core.plan_limits import enforce_plan_limit, is_within_plan_limit
    from apps.api.db.models import CallCampaign
    from apps.api.db.models.usage_event import UsageEvent

    await _paid_plan(db_session, max_call_minutes=10, max_voice_campaigns=1)
    # 5 minutes used: still within the 10-minute limit.
    db_session.add(
        UsageEvent(
            event_id="e1",
            org_id=ORG_ID,
            service="voice",
            usage_type="voice_seconds",
            quantity=300,
            unit="second",
            source="test",
        )
    )
    await db_session.commit()
    assert await is_within_plan_limit(db_session, ORG_ID, "call_minutes")

    db_session.add(
        UsageEvent(
            event_id="e2",
            org_id=ORG_ID,
            service="voice",
            usage_type="voice_seconds",
            quantity=300,
            unit="second",
            source="test",
        )
    )
    db_session.add(CallCampaign(org_id=ORG_ID, name="c1", criteria="x", channel="voice"))
    await db_session.commit()
    assert not await is_within_plan_limit(db_session, ORG_ID, "call_minutes")
    with pytest.raises(HTTPException) as exc:
        await enforce_plan_limit(db_session, ORG_ID, "voice_campaigns")
    assert exc.value.status_code == 403 and exc.value.detail["error"] == "plan_limit_reached"
    # Unlimited resource on the same plan is untouched.
    await enforce_plan_limit(db_session, ORG_ID, "whatsapp_campaigns")


async def test_owner_org_exempt_from_limits(db_session: AsyncSession) -> None:
    from apps.api.core.plan_limits import enforce_plan_limit
    from apps.api.core.security import generate_login_token, hash_token
    from apps.api.db.models import AccountUser, OrgMembership

    await _paid_plan(db_session, max_voice_campaigns=0)
    account = AccountUser(
        email="owner2@veerox.example",
        token_hash=hash_token(generate_login_token()),
        is_superuser=True,
    )
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=ORG_ID, account_user_id=account.id, role="admin"))
    await db_session.commit()
    await enforce_plan_limit(db_session, ORG_ID, "voice_campaigns")  # owner: never limited


async def test_plan_required_flag(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_session.add(Org(id=ORG_ID, name="Gate Co"))
    await db_session.commit()

    monkeypatch.setattr(settings, "plan_required_on_login", False)
    assert (await client.get("/payments/status", headers=ADMIN)).json()["plan_required"] is False

    monkeypatch.setattr(settings, "plan_required_on_login", True)
    body = (await client.get("/payments/status", headers=ADMIN)).json()
    assert body["active"] is False and body["plan_required"] is True


async def test_addon_pack_raises_only_its_resource(db_session: AsyncSession) -> None:
    from sqlalchemy import select

    from apps.api.core.plan_limits import get_resource_usage
    from apps.api.db.models.razorpay_payment import PaymentPlan, RazorpayPayment

    await _paid_plan(db_session, max_call_minutes=10, max_team_members=3)
    base = (await db_session.execute(select(RazorpayPayment))).scalar_one()
    addon = PaymentPlan(
        code="mins50", name="+50 min", price_paise=100, duration_days=30,
        is_addon=True, max_call_minutes=50,
    )
    db_session.add(addon)
    await db_session.flush()
    db_session.add(
        RazorpayPayment(
            org_id=ORG_ID, plan_id=addon.id, razorpay_order_id="order_addon",
            amount_paise=100, status="paid",
            period_start=base.period_start, period_end=base.period_end,
        )
    )
    await db_session.commit()
    calls = await get_resource_usage(db_session, ORG_ID, "call_minutes")
    team = await get_resource_usage(db_session, ORG_ID, "team_members")
    assert calls is not None and calls.limit == 60  # 10 base + 50 add-on
    assert team is not None and team.limit == 3  # untouched


async def test_campaign_traffic_not_counted_in_message_or_minute_limits(
    db_session: AsyncSession,
) -> None:
    """Campaign calls/sends are limited by the campaign counts only."""
    from apps.api.core.plan_limits import get_resource_usage
    from apps.api.db.models.usage_event import UsageEvent

    await _paid_plan(db_session, max_call_minutes=1, max_whatsapp_messages=2)

    def ev(eid: str, usage_type: str, qty: int, source: str, meta: dict | None = None) -> UsageEvent:
        return UsageEvent(
            event_id=eid, org_id=ORG_ID, service="x", usage_type=usage_type,
            quantity=qty, unit="u", source=source, metadata_json=meta,
        )

    db_session.add_all(
        [
            ev("c1", "voice_seconds", 600, "realtime_bridge", {"campaign": True}),  # campaign call
            ev("c2", "voice_seconds", 30, "realtime_bridge"),  # normal call
            ev("m1", "whatsapp_messages", 1, "whatsapp_dispatcher"),  # campaign send
            ev("m2", "whatsapp_messages", 1, "whatsapp_dispatcher"),
            ev("m3", "whatsapp_messages", 1, "chats_send"),  # normal send
        ]
    )
    await db_session.commit()
    calls = await get_resource_usage(db_session, ORG_ID, "call_minutes")
    msgs = await get_resource_usage(db_session, ORG_ID, "whatsapp_messages")
    assert calls is not None and calls.used == 0.5  # only the 30s normal call
    assert msgs is not None and msgs.used == 1  # only the normal send


async def test_admin_can_delete_unbought_plan_but_not_a_bought_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from datetime import UTC, datetime, timedelta

    from apps.api.db.models.razorpay_payment import RazorpayPayment

    db_session.add(Org(id=ORG_ID, name="Del Co"))
    await db_session.commit()

    def body(code: str) -> dict:
        return {"code": code, "name": code, "price_paise": 100, "duration_days": 30}

    free = (await client.post("/payments/admin/plans", headers=ADMIN, json=body("free"))).json()
    bought = (await client.post("/payments/admin/plans", headers=ADMIN, json=body("bought"))).json()
    now = datetime.now(UTC)
    db_session.add_all(
        [
            # an abandoned checkout must not block deleting its plan
            RazorpayPayment(org_id=ORG_ID, plan_id=uuid.UUID(free["id"]), razorpay_order_id="o_abandoned",
                            amount_paise=100, status="created"),
            RazorpayPayment(org_id=ORG_ID, plan_id=uuid.UUID(bought["id"]), razorpay_order_id="o_paid",
                            amount_paise=100, status="paid", period_start=now,
                            period_end=now + timedelta(days=30)),
        ]
    )
    await db_session.commit()

    ok = await client.delete(f"/payments/admin/plans/{free['id']}", headers=ADMIN)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    blocked = await client.delete(f"/payments/admin/plans/{bought['id']}", headers=ADMIN)
    assert blocked.status_code == 409 and "Hide" in blocked.json()["detail"]
    assert (await client.delete(f"/payments/admin/plans/{free['id']}", headers=ADMIN)).status_code == 404
    codes = [p["code"] for p in (await client.get("/payments/admin/plans", headers=ADMIN)).json()]
    assert codes == ["bought"]
