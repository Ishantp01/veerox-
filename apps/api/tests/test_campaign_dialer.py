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

from apps.api.channels.voice.org_numbers import replace_org_phone_numbers
from apps.api.db.models import CallCampaign, CampaignTarget, Org
from apps.api.schemas.org_numbers import OrgPhoneNumberIn
from apps.api.workers import campaign_dialer
from apps.api.workers.campaign_dialer import handle_call_ended

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]


@pytest_asyncio.fixture(autouse=True)
async def _redirect_dialer_sessions(test_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """handle_call_ended opens its own AsyncSessionLocal() rather than taking
    a ``db`` argument — point it at the test engine so it shares the same
    in-memory SQLite the ``db_session`` fixture writes/reads through."""
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(campaign_dialer, "AsyncSessionLocal", session_factory)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(campaign_dialer, "get_redis_pool", lambda: fake)
    return fake


async def _seed_org(db: AsyncSession, *, preferred_voice_provider: str | None = None) -> None:
    db.add(Org(id=ORG_ID, name="Test Org", preferred_voice_provider=preferred_voice_provider))
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
        channel=campaign_channel,
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


async def test_count_calls_in_flight_ignores_whatsapp_campaigns(db_session: AsyncSession) -> None:
    """CampaignTarget.status="calling" is shared with the WhatsApp dispatcher
    — an in-progress WhatsApp conversation must never count against the
    voice dialer's concurrency budget."""
    await _seed_org(db_session)
    await _seed_target(db_session, status="calling", campaign_channel="whatsapp")

    assert await campaign_dialer._count_calls_in_flight(db_session) == 0


async def test_count_calls_in_flight_counts_voice_calling_targets(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    await _seed_target(db_session, status="calling", campaign_channel="voice")

    assert await campaign_dialer._count_calls_in_flight(db_session) == 1


async def test_claim_targets_skips_whatsapp_campaigns(db_session: AsyncSession) -> None:
    """A pending WhatsApp campaign target must never be claimed (and dialed)
    by the voice dialer — it belongs to whatsapp_dispatcher instead."""
    await _seed_org(db_session)
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="whatsapp")

    claimed = await campaign_dialer._claim_targets()

    assert claimed == []


async def test_claim_targets_claims_pending_voice_campaign(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    target = await _seed_target(
        db_session, status="pending", attempt_count=0, campaign_channel="voice"
    )

    claimed = await campaign_dialer._claim_targets()

    assert len(claimed) == 1
    target_id, phone, attempt_count, plivo_from, twilio_from, preferred_provider, org_id = claimed[0]
    assert target_id == str(target.id)
    assert phone == target.phone
    assert attempt_count == 1
    assert plivo_from is None
    assert twilio_from is None
    assert preferred_provider is None
    assert org_id == target.org_id


async def test_claim_targets_carries_org_preferred_provider(db_session: AsyncSession) -> None:
    """The org's explicit Plivo/Twilio override must ride along with each
    claimed target so _dial_one can pass it to initiate_call — otherwise a
    campaign for an org that set a preference would silently ignore it."""
    await _seed_org(db_session, preferred_voice_provider="twilio")
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")

    claimed = await campaign_dialer._claim_targets()

    assert len(claimed) == 1
    *_, preferred_provider, org_id = claimed[0]
    assert preferred_provider == "twilio"


async def test_claim_targets_rotates_across_two_plivo_numbers_over_separate_ticks(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """Two dialer ticks, one pending target each, must alternate between the
    org's two Plivo numbers rather than both dialing from the same one."""
    await _seed_org(db_session)
    await replace_org_phone_numbers(
        db_session,
        ORG_ID,
        [
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001"),
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550002"),
        ],
    )
    await db_session.commit()

    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")
    first_claimed = await campaign_dialer._claim_targets()
    assert len(first_claimed) == 1
    first_plivo_from = first_claimed[0][3]

    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")
    second_claimed = await campaign_dialer._claim_targets()
    assert len(second_claimed) == 1
    second_plivo_from = second_claimed[0][3]

    assert {first_plivo_from, second_plivo_from} == {"+14155550001", "+14155550002"}
    assert first_plivo_from != second_plivo_from


async def test_claim_targets_rotates_within_a_single_batch(
    db_session: AsyncSession, fake_redis: _FakeRedis
) -> None:
    """Two pending targets claimed together in one poll tick must still get
    distinct from-numbers, not both the first one in rotation order."""
    await _seed_org(db_session)
    await replace_org_phone_numbers(
        db_session,
        ORG_ID,
        [
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001"),
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550002"),
        ],
    )
    await db_session.commit()
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")

    claimed = await campaign_dialer._claim_targets()

    assert len(claimed) == 2
    plivo_froms = [row[3] for row in claimed]
    assert set(plivo_froms) == {"+14155550001", "+14155550002"}


async def test_claim_targets_stops_at_concurrency_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With max_concurrent_calls=2 and one call already in flight, only one
    more pending target should be claimed even though two are pending."""
    from apps.api.config import settings

    monkeypatch.setattr(settings, "max_concurrent_calls", 2)
    await _seed_org(db_session)
    await _seed_target(db_session, status="calling", campaign_channel="voice")
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")
    await _seed_target(db_session, status="pending", attempt_count=0, campaign_channel="voice")

    claimed = await campaign_dialer._claim_targets()

    assert len(claimed) == 1
