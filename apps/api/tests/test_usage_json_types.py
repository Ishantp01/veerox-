"""Regression test for a real bug caught during review: Pydantic v2
serializes `Decimal`-typed response fields to JSON as *strings*, not
numbers. schemas/usage.py's response models must use `float` (matching
every other money/quantity field in this codebase, e.g. schemas/lead.py's
deal_value) so the frontend's `number`-typed hooks and formatUsd() (which
checks `typeof amount === "number"`) don't silently render every dollar
amount as $0.00.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership
from apps.api.db.models.usage_monthly import UsageMonthly

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _seed_org_with_session(db_session: AsyncSession) -> tuple[Org, str]:
    org = Org(name="JSON Types Co")
    db_session.add(org)
    await db_session.flush()
    token = generate_login_token()
    account = AccountUser(email="admin@jsontypes.example", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    db_session.add(
        UsageMonthly(
            org_id=org.id,
            billing_period="2026-09",
            service="voice",
            usage_type="voice_seconds",
            total_quantity=12.5,
            third_party_cost=1.5,
            calculation_status="calculated",
        )
    )
    await db_session.commit()
    return org, token


async def test_org_usage_response_fields_are_json_numbers_not_strings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org, token = await _seed_org_with_session(db_session)
    login = await client.post("/auth/login", json={"token": token})
    session_token = login.json()["token"]

    resp = await client.get(
        "/org/usage?period=2026-09", headers={"X-Session-Token": session_token}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert isinstance(body["third_party_cost_total"], (int, float))
    assert isinstance(body["estimated_infrastructure_usage"], (int, float))
    assert isinstance(body["platform_fee"], (int, float))
    assert isinstance(body["estimated_total_charge"], (int, float))
    assert isinstance(body["breakdown"][0]["total_quantity"], (int, float))
    assert isinstance(body["breakdown"][0]["estimated_cost"], (int, float))
    assert body["breakdown"][0]["total_quantity"] == 12.5


async def test_admin_aws_costs_response_fields_are_json_numbers_not_strings(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org_with_session(db_session)

    resp = await client.get("/admin/aws-costs?period=2026-09", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()

    assert isinstance(body["actual_aws_total"], (int, float))
    assert isinstance(body["allocated_org_total"], (int, float))
    assert isinstance(body["shared_unallocated_total"], (int, float))
    assert isinstance(body["difference"], (int, float))


async def test_internal_usage_event_ingestion_accepts_decimal_quantity(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The request-body schema (UsageEventIn) intentionally stays Decimal
    (ingestion precision) — confirm that still round-trips correctly."""
    org = Org(name="Ingest Co")
    db_session.add(org)
    await db_session.commit()

    resp = await client.post(
        "/internal/usage-events",
        headers=ADMIN_HEADERS,
        json={
            "organization_id": str(org.id),
            "event_id": str(uuid.uuid4()),
            "service": "storage",
            "usage_type": "storage_gb_month",
            "quantity": "0.0000000123",
            "unit": "gb",
            "source": "test",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["recorded"] is True
