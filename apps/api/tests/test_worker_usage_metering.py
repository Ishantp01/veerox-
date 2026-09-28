"""Covers the `_record_worker_job_seconds` usage-metering helper added to
workers/campaign_dialer.py, workers/whatsapp_dispatcher.py, and
workers/follow_up_dispatcher.py (req §6's AWS-workload bullet):
worker_job_seconds is recorded, split evenly across every org touched in
that tick, and a metering failure never raises past the helper."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.db.models import Org
from apps.api.db.models.usage_event import UsageEvent


@pytest.fixture(autouse=True)
def _redirect_worker_sessions(test_engine, monkeypatch: pytest.MonkeyPatch):
    from apps.api.workers import campaign_dialer, follow_up_dispatcher, whatsapp_dispatcher

    test_session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    for module in (campaign_dialer, whatsapp_dispatcher, follow_up_dispatcher):
        monkeypatch.setattr(module, "AsyncSessionLocal", test_session_factory)


async def _seed_two_orgs(db_session: AsyncSession) -> tuple[Org, Org]:
    org_a = Org(name="Metered Dialer Co")
    org_b = Org(name="Metered Dispatcher Co")
    db_session.add_all([org_a, org_b])
    await db_session.commit()
    return org_a, org_b


async def test_campaign_dialer_records_worker_job_seconds_split_across_orgs(
    db_session: AsyncSession,
) -> None:
    from apps.api.workers.campaign_dialer import _record_worker_job_seconds

    org_a, org_b = await _seed_two_orgs(db_session)

    await _record_worker_job_seconds({org_a.id, org_b.id}, 10.0)

    rows = (
        await db_session.execute(
            select(UsageEvent).where(
                UsageEvent.service == "compute", UsageEvent.source == "campaign_dialer"
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {r.org_id for r in rows} == {org_a.id, org_b.id}
    for row in rows:
        assert row.quantity == Decimal("5")
        assert row.usage_type == "worker_job_seconds"


async def test_whatsapp_dispatcher_records_worker_job_seconds(db_session: AsyncSession) -> None:
    from apps.api.workers.whatsapp_dispatcher import _record_worker_job_seconds

    org_a, _org_b = await _seed_two_orgs(db_session)

    await _record_worker_job_seconds({org_a.id}, 4.0)

    row = (
        await db_session.execute(
            select(UsageEvent).where(
                UsageEvent.service == "compute", UsageEvent.source == "whatsapp_dispatcher"
            )
        )
    ).scalar_one()
    assert row.org_id == org_a.id
    assert row.quantity == Decimal("4")


async def test_follow_up_dispatcher_records_worker_job_seconds(db_session: AsyncSession) -> None:
    from apps.api.workers.follow_up_dispatcher import _record_worker_job_seconds

    org_a, _org_b = await _seed_two_orgs(db_session)

    await _record_worker_job_seconds({org_a.id}, 2.5)

    row = (
        await db_session.execute(
            select(UsageEvent).where(
                UsageEvent.service == "compute", UsageEvent.source == "follow_up_dispatcher"
            )
        )
    ).scalar_one()
    assert row.org_id == org_a.id
    assert row.quantity == Decimal("2.5")


async def test_no_orgs_means_no_event_recorded(db_session: AsyncSession) -> None:
    """An empty tick (nothing claimed) must not write a zero-org event."""
    from apps.api.workers.campaign_dialer import _record_worker_job_seconds

    await _record_worker_job_seconds(set(), 5.0)

    rows = (
        await db_session.execute(
            select(UsageEvent).where(UsageEvent.source == "campaign_dialer")
        )
    ).scalars().all()
    assert rows == []


async def test_metering_failure_does_not_raise(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A metering failure (e.g. a transient DB error) must never propagate
    out of the helper and break the worker tick that already completed its
    real work."""
    from apps.api.workers import campaign_dialer

    org = Org(name="Failure Co")
    db_session.add(org)
    await db_session.commit()

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(campaign_dialer, "record_usage", _boom)

    # Must not raise.
    await campaign_dialer._record_worker_job_seconds({org.id}, 1.0)
