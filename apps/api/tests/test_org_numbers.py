"""Tests for channels/voice/org_numbers.py's round-robin call placement.

Covers get_rotating_numbers/next_rotating_number/get_numbers_by_org — an org
with 2+ dedicated numbers per provider must round-robin outbound calls
across all of them (call 1 -> number A, call 2 -> number B, back to A on
call 3, ...) instead of always dialing from the single is_default
("Primary") one. A single number per provider must behave exactly like the
old always-use-the-default behavior, including never touching Redis.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice.org_numbers import (
    get_numbers_by_org,
    get_rotating_numbers,
    next_rotating_number,
    replace_org_phone_numbers,
)
from apps.api.db.models import Org, OrgPhoneNumber
from apps.api.schemas.org_numbers import OrgPhoneNumberIn

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_ID_2 = uuid.UUID("00000000-0000-0000-0000-000000000002")


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]


async def _seed_org(db: AsyncSession, org_id: uuid.UUID = ORG_ID) -> None:
    db.add(Org(id=org_id, name="Test Org"))
    await db.commit()


async def test_get_rotating_numbers_single_number_never_touches_redis(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    db_session.add(OrgPhoneNumber(org_id=ORG_ID, provider="plivo", phone_number="14155550001"))
    await db_session.commit()

    redis = _FakeRedis()
    plivo_from, twilio_from = await get_rotating_numbers(db_session, redis, ORG_ID)

    assert plivo_from == "+14155550001"
    assert twilio_from is None
    assert redis.kv == {}


async def test_get_rotating_numbers_alternates_across_two_plivo_numbers(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    await replace_org_phone_numbers(
        db_session,
        ORG_ID,
        [
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001", is_default=True),
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550002"),
        ],
    )
    await db_session.commit()

    redis = _FakeRedis()
    calls = [(await get_rotating_numbers(db_session, redis, ORG_ID))[0] for _ in range(4)]

    assert calls == ["+14155550001", "+14155550002", "+14155550001", "+14155550002"]


async def test_get_rotating_numbers_rotates_plivo_and_twilio_independently(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    await replace_org_phone_numbers(
        db_session,
        ORG_ID,
        [
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001"),
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550002"),
            OrgPhoneNumberIn(provider="twilio", phone_number="+14155559001"),
            OrgPhoneNumberIn(provider="twilio", phone_number="+14155559002"),
        ],
    )
    await db_session.commit()

    redis = _FakeRedis()
    first = await get_rotating_numbers(db_session, redis, ORG_ID)
    second = await get_rotating_numbers(db_session, redis, ORG_ID)

    assert first == ("+14155550001", "+14155559001")
    assert second == ("+14155550002", "+14155559002")


async def test_replace_org_phone_numbers_sets_position_per_provider(
    db_session: AsyncSession,
) -> None:
    """Rotation order must follow submission order, not created_at — several
    rows written in one call land in the same transaction/timestamp (see
    db/models/org_phone_number.py's docstring)."""
    await _seed_org(db_session)
    await replace_org_phone_numbers(
        db_session,
        ORG_ID,
        [
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001"),
            OrgPhoneNumberIn(provider="twilio", phone_number="+14155559001"),
            OrgPhoneNumberIn(provider="plivo", phone_number="+14155550002"),
        ],
    )
    await db_session.commit()

    numbers_by_org = await get_numbers_by_org(db_session, [ORG_ID])
    assert numbers_by_org[ORG_ID]["plivo"] == ["14155550001", "14155550002"]
    assert numbers_by_org[ORG_ID]["twilio"] == ["14155559001"]


async def test_get_numbers_by_org_bulk_fetches_multiple_orgs(db_session: AsyncSession) -> None:
    await _seed_org(db_session, ORG_ID)
    await _seed_org(db_session, ORG_ID_2)
    await replace_org_phone_numbers(
        db_session, ORG_ID, [OrgPhoneNumberIn(provider="plivo", phone_number="+14155550001")]
    )
    await replace_org_phone_numbers(
        db_session, ORG_ID_2, [OrgPhoneNumberIn(provider="plivo", phone_number="+14155559999")]
    )
    await db_session.commit()

    result = await get_numbers_by_org(db_session, [ORG_ID, ORG_ID_2])
    assert result[ORG_ID]["plivo"] == ["14155550001"]
    assert result[ORG_ID_2]["plivo"] == ["14155559999"]


async def test_get_numbers_by_org_empty_org_ids_returns_empty_dict(
    db_session: AsyncSession,
) -> None:
    assert await get_numbers_by_org(db_session, []) == {}


async def test_next_rotating_number_empty_list_returns_none() -> None:
    redis = _FakeRedis()
    assert await next_rotating_number(redis, ORG_ID, "plivo", []) is None
