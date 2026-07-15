"""Tests for apps.api.workers.campaign_dialer's call-outcome handling.

Covers the "never re-dial someone who already answered" invariant:
handle_call_ended must only retry (status="pending") when no Conversation
ever got attached to the target — a connected call always resolves to
"failed" regardless of remaining attempts.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.db.models import CallCampaign, CampaignTarget, Org
from apps.api.workers import campaign_dialer
from apps.api.workers.campaign_dialer import handle_call_ended

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture(autouse=True)
async def _redirect_dialer_sessions(test_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_call_ended opens its own AsyncSessionLocal() rather than taking
    a ``db`` argument — point it at the test engine so it shares the same
    in-memory SQLite the ``db_session`` fixture writes/reads through."""
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(campaign_dialer, "AsyncSessionLocal", session_factory)


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def _seed_target(
    db: AsyncSession,
    *,
    status: str = "calling",
    attempt_count: int = 1,
    campaign_channel: str = "voice",
    campaign_status: str = "running",
    **kwargs,
) -> CampaignTarget:
    campaign = CallCampaign(
        org_id=ORG_ID, name="Test Campaign", criteria="n/a",
        channel=campaign_channel, status=campaign_status,
    )
    db.add(campaign)
    await db.flush()
    target = CampaignTarget(
        campaign_id=campaign.id,
        org_id=ORG_ID,
        phone="+919000000001",
        status=status,
        attempt_count=attempt_count,
        **kwargs,
    )
    db.add(target)
    await db.commit()
    return target


async def test_handle_call_ended_retries_when_never_connected(db_session: AsyncSession) -> None:
    """No conversation_id attached -> call never connected -> retry (pending)."""
    await _seed_org(db_session)
    target = await _seed_target(db_session)

    await handle_call_ended(str(target.id))

    await db_session.refresh(target)
    assert target.status == "pending"


async def test_handle_call_ended_fails_permanently_after_max_attempts(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    target = await _seed_target(db_session, attempt_count=3)

    await handle_call_ended(str(target.id))

    await db_session.refresh(target)
    assert target.status == "failed"


async def test_handle_call_ended_never_retries_a_connected_call(
    db_session: AsyncSession,
) -> None:
    """conversation_id set -> prospect actually answered -> always failed,
    never re-queued, even with attempts remaining."""
    await _seed_org(db_session)
    target = await _seed_target(db_session, attempt_count=1, conversation_id=uuid.uuid4())

    await handle_call_ended(str(target.id))

    await db_session.refresh(target)
    assert target.status == "failed"


async def test_handle_call_ended_noop_when_already_resolved(db_session: AsyncSession) -> None:
    """qualify_lead already flipped status to "completed" -> hangup webhook
    firing afterward (or racing) must not clobber it."""
    await _seed_org(db_session)
    target = await _seed_target(db_session, status="completed", qualified=True)

    await handle_call_ended(str(target.id))

    await db_session.refresh(target)
    assert target.status == "completed"


async def test_any_call_in_flight_ignores_whatsapp_campaigns(db_session: AsyncSession) -> None:
    """CampaignTarget.status="calling" is shared with the WhatsApp dispatcher
    — an in-progress WhatsApp conversation must never block the voice
    dialer's sequential gate."""
    await _seed_org(db_session)
    await _seed_target(db_session, status="calling", campaign_channel="whatsapp")

    assert await campaign_dialer._any_call_in_flight(db_session) is False


async def test_any_call_in_flight_true_for_voice_calling_target(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    await _seed_target(db_session, status="calling", campaign_channel="voice")

    assert await campaign_dialer._any_call_in_flight(db_session) is True


async def test_claim_next_target_skips_whatsapp_campaigns(db_session: AsyncSession) -> None:
    """A pending WhatsApp campaign target must never be claimed (and dialed)
    by the voice dialer — it belongs to whatsapp_dispatcher instead."""
    await _seed_org(db_session)
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="whatsapp")

    claimed = await campaign_dialer._claim_next_target()

    assert claimed is None


async def test_claim_next_target_claims_pending_voice_campaign(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    target = await _seed_target(
        db_session, status="pending", attempt_count=0, campaign_channel="voice"
    )

    claimed = await campaign_dialer._claim_next_target()

    assert claimed is not None
    target_id, phone, attempt_count = claimed
    assert target_id == str(target.id)
    assert phone == target.phone
    assert attempt_count == 1
