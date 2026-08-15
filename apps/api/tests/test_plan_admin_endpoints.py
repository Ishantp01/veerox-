from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models import Org, Plan

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed_plan(db: AsyncSession) -> Plan:
    plan = Plan(
        code="pro",
        name="Pro",
        price_cents=490000,
        limits={"max_seats": 5},
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def test_list_plans_requires_admin(client: AsyncClient) -> None:
    response = await client.get("/billing/plans")
    assert response.status_code == 403


async def test_create_list_update_plan(client: AsyncClient, db_session: AsyncSession) -> None:
    create_response = await client.post(
        "/billing/plans",
        json={
            "code": "enterprise",
            "name": "Enterprise",
            "price_cents": 4990000,
            "limits": {"max_seats": 100},
        },
        headers=ADMIN_HEADERS,
    )
    assert create_response.status_code == 201
    assert create_response.json()["code"] == "enterprise"

    duplicate_response = await client.post(
        "/billing/plans",
        json={
            "code": "enterprise",
            "name": "Enterprise Again",
            "price_cents": 1,
            "limits": {},
        },
        headers=ADMIN_HEADERS,
    )
    assert duplicate_response.status_code == 400

    list_response = await client.get("/billing/plans", headers=ADMIN_HEADERS)
    assert list_response.status_code == 200
    codes = {p["code"] for p in list_response.json()}
    assert "enterprise" in codes

    update_response = await client.patch(
        "/billing/plans/enterprise",
        json={"limits": {"max_seats": 200}},
        headers=ADMIN_HEADERS,
    )
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["limits"]["max_seats"] == 200


async def test_update_unknown_plan_404(client: AsyncClient) -> None:
    response = await client.patch(
        "/billing/plans/does-not-exist", json={"name": "x"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 404


async def test_delete_unassigned_plan(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_plan(db_session)
    response = await client.delete("/billing/plans/pro", headers=ADMIN_HEADERS)
    assert response.status_code == 204

    result = await db_session.execute(select(Plan).where(Plan.code == "pro"))
    assert result.scalar_one_or_none() is None


async def test_delete_plan_in_use_is_blocked(client: AsyncClient, db_session: AsyncSession) -> None:
    plan = await _seed_plan(db_session)
    db_session.add(Org(name="Uses Pro", plan_id=plan.id))
    await db_session.commit()

    response = await client.delete("/billing/plans/pro", headers=ADMIN_HEADERS)
    assert response.status_code == 400

    result = await db_session.execute(select(Plan).where(Plan.code == "pro"))
    assert result.scalar_one_or_none() is not None


async def test_create_paid_plan_never_touches_razorpay(client: AsyncClient) -> None:
    # Plans have no Razorpay-side counterpart anymore — Orders are created
    # ad hoc at checkout time, so plan creation never calls out to Razorpay
    # (and doesn't need credentials configured at all).
    response = await client.post(
        "/billing/plans",
        json={
            "code": "unconfigured-paid",
            "name": "Unconfigured Paid",
            "price_cents": 100000,
            "limits": {},
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201


async def test_create_free_plan(client: AsyncClient) -> None:
    response = await client.post(
        "/billing/plans",
        json={
            "code": "totally-free",
            "name": "Totally Free",
            "price_cents": 0,
            "limits": {},
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
