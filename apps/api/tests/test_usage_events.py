"""Covers core/usage.py::record_usage — the ledger insert, idempotency on
duplicate `event_id`, and the metadata secret-key denylist."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.usage import UsageMetadataViolation, deterministic_event_id, record_usage
from apps.api.db.models import Org
from apps.api.db.models.usage_event import UsageEvent


async def _seed_org(db_session: AsyncSession) -> Org:
    org = Org(name="Metered Co")
    db_session.add(org)
    await db_session.commit()
    return org


async def test_record_usage_inserts_one_row(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)

    event = await record_usage(
        db_session,
        organization_id=org.id,
        event_id=deterministic_event_id("test", "abc"),
        service="voice",
        usage_type="voice_seconds",
        quantity=42,
        unit="seconds",
        source="test",
    )
    await db_session.commit()

    assert event is not None
    result = await db_session.execute(select(UsageEvent).where(UsageEvent.org_id == org.id))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].quantity == 42


async def test_duplicate_event_id_is_ignored_not_double_counted(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    event_id = deterministic_event_id("voice_call_ended", "call-123")

    first = await record_usage(
        db_session,
        organization_id=org.id,
        event_id=event_id,
        service="voice",
        usage_type="voice_seconds",
        quantity=10,
        unit="seconds",
        source="test",
    )
    await db_session.commit()
    assert first is not None

    # Simulate a retry (e.g. a redelivered webhook) with the same deterministic id.
    second = await record_usage(
        db_session,
        organization_id=org.id,
        event_id=event_id,
        service="voice",
        usage_type="voice_seconds",
        quantity=10,
        unit="seconds",
        source="test",
    )
    await db_session.commit()
    assert second is None

    result = await db_session.execute(select(UsageEvent).where(UsageEvent.org_id == org.id))
    rows = result.scalars().all()
    assert len(rows) == 1


async def test_deterministic_event_id_is_stable() -> None:
    assert deterministic_event_id("a", "b") == deterministic_event_id("a", "b")
    assert deterministic_event_id("a", "b") != deterministic_event_id("a", "c")


async def test_sensitive_metadata_key_is_rejected(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    with pytest.raises(UsageMetadataViolation):
        await record_usage(
            db_session,
            organization_id=org.id,
            event_id=deterministic_event_id("test", "leak"),
            service="ai",
            usage_type="ai_request_count",
            quantity=1,
            unit="requests",
            source="test",
            metadata={"api_key": "sk-should-not-be-here"},
        )


async def test_unknown_service_is_rejected(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    with pytest.raises(ValueError):
        await record_usage(
            db_session,
            organization_id=org.id,
            event_id=deterministic_event_id("test", "bad"),
            service="not_a_real_service",
            usage_type="ai_request_count",
            quantity=1,
            unit="requests",
            source="test",
        )
