"""Covers GET/POST /admin/cost-rates (req §7/§9's "admin-editable" CostRate
table) — a real gap found on review: CostRate's own docstring claimed these
endpoints already existed, but nothing had actually been built, so
estimated_cost could never be anything but NULL. Also covers that creating
a new rate for the same (service, usage_type, provider) key closes out the
prior still-open one, and that non-superuser sessions can't reach it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership
from apps.api.db.models.cost_rate import CostRate

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def test_create_and_list_cost_rate(client: AsyncClient) -> None:
    create_resp = await client.post(
        "/admin/cost-rates",
        headers=ADMIN_HEADERS,
        json={
            "service": "ai",
            "usage_type": "ai_input_tokens",
            "provider": "openai",
            "unit_cost": "0.0000025",
            "currency": "USD",
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["service"] == "ai"
    assert body["unit_cost"] == 0.0000025
    assert body["effective_to"] is None

    list_resp = await client.get("/admin/cost-rates", headers=ADMIN_HEADERS)
    assert list_resp.status_code == 200
    rates = list_resp.json()
    assert any(r["id"] == body["id"] for r in rates)


async def test_creating_a_new_rate_closes_out_the_prior_open_one(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await client.post(
        "/admin/cost-rates",
        headers=ADMIN_HEADERS,
        json={
            "service": "voice",
            "usage_type": "voice_seconds",
            "provider": "plivo",
            "unit_cost": "0.01",
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    later = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    second = await client.post(
        "/admin/cost-rates",
        headers=ADMIN_HEADERS,
        json={
            "service": "voice",
            "usage_type": "voice_seconds",
            "provider": "plivo",
            "unit_cost": "0.015",
            "effective_from": later,
        },
    )
    assert second.status_code == 201
    assert second.json()["effective_to"] is None

    from uuid import UUID

    row = await db_session.get(CostRate, UUID(first_id))
    assert row is not None
    await db_session.refresh(row)
    assert row.effective_to is not None  # closed out by the second create


async def test_naive_effective_from_is_treated_as_utc(client: AsyncClient) -> None:
    """Regression test: a client-supplied `effective_from` with no UTC
    offset (natural for a human admin to type) must not be stored as a
    naive datetime — see admin_create_cost_rate's docstring.

    Checked against the create response's own `effective_from` (computed
    in-process, before any DB round-trip) rather than re-reading the row —
    SQLite (used in tests) doesn't persist tzinfo on a DateTime(timezone=True)
    column even for a value that *was* written tz-aware (see
    routers/billing.py's `_as_aware_utc` for the same, pre-existing
    limitation elsewhere in this codebase), so asserting on a re-fetched
    row's `.tzinfo` here would fail for a reason unrelated to this fix.
    """
    resp = await client.post(
        "/admin/cost-rates",
        headers=ADMIN_HEADERS,
        json={
            "service": "database",
            "usage_type": "database_operations",
            "unit_cost": "0.0001",
            "effective_from": "2026-10-01T00:00:00",
        },
    )
    assert resp.status_code == 201
    # A UTC-normalized value serializes with an explicit offset
    # ("+00:00"); a naive one would serialize as "2026-10-01T00:00:00"
    # with no offset at all.
    assert resp.json()["effective_from"] == "2026-10-01T00:00:00+00:00"


async def test_non_superuser_session_cannot_manage_cost_rates(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = Org(name="Rate Peeker Co")
    db_session.add(org)
    await db_session.flush()
    token = generate_login_token()
    account = AccountUser(email="admin@ratepeeker.example", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login = await client.post("/auth/login", json={"token": token})
    session_token = login.json()["token"]

    resp = await client.get("/admin/cost-rates", headers={"X-Session-Token": session_token})
    assert resp.status_code == 403


async def test_cost_rate_feeds_into_estimated_cost(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """End-to-end: without a rate, estimated_cost is NULL; with one, the
    daily aggregator prices the usage — closing the exact gap this test
    file's docstring describes."""
    from apps.api.core.usage import deterministic_event_id, record_usage
    from apps.api.workers.usage_daily_aggregator import _unit_cost

    org = Org(name="Priced Co")
    db_session.add(org)
    await db_session.flush()
    await record_usage(
        db_session,
        organization_id=org.id,
        event_id=deterministic_event_id("test_rate", "1"),
        service="ai",
        usage_type="ai_output_tokens",
        quantity=1000,
        unit="tokens",
        provider="openai",
        source="test",
    )
    await db_session.commit()

    # No rate configured yet — nothing to price with.
    unpriced = await _unit_cost(db_session, "ai", "ai_output_tokens", "openai", datetime.now(UTC))
    assert unpriced is None

    create_resp = await client.post(
        "/admin/cost-rates",
        headers=ADMIN_HEADERS,
        json={
            "service": "ai",
            "usage_type": "ai_output_tokens",
            "provider": "openai",
            "unit_cost": "0.00001",
        },
    )
    assert create_resp.status_code == 201

    cost = await _unit_cost(db_session, "ai", "ai_output_tokens", "openai", datetime.now(UTC))
    assert cost is not None
    assert float(cost) == 0.00001
