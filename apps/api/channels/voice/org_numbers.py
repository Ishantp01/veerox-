"""Reads/writes for an org's OrgPhoneNumber rows — the dedicated Plivo/Twilio
numbers it dials from and can be reached on. Shared by every write path
(routers/auth.py::provision_org, routers/billing.py::update_org,
routers/admin.py::update_org_numbers) and every outbound-calling entry point
(routers/admin.py::outbound_call, workers/follow_up_dispatcher.py,
core/tools.py's AI callback tool — workers/campaign_dialer.py is the one
exception, since its claim query needs these joined in-line for performance,
see its own docstring).

Call placement itself uses get_rotating_numbers (or campaign_dialer's own
batched get_numbers_by_org/next_rotating_number), not get_default_numbers —
an org with 2+ numbers on a provider round-robins across all of them rather
than always dialing from the single "Primary" one. get_default_numbers is
kept for display purposes (the "Primary" badge in settings).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.schemas.org_numbers import OrgPhoneNumberIn

# Per-(org, provider) rotation counter — see get_rotating_numbers. Same
# INCR-then-modulo pattern as core/tools.py's escalation round robin
# (_TRANSFER_ROUND_ROBIN_PREFIX): the counter only ever grows, so numbers can
# be added/removed without resetting it.
_PHONE_ROUND_ROBIN_PREFIX = "veerox:phone_round_robin:"


async def get_default_numbers(db: AsyncSession, org_id: UUID | str) -> tuple[str | None, str | None]:
    """(plivo_from_e164, twilio_from_e164) — this org's *default* ("Primary")
    number per provider, or None if it has none on that provider. Purely a
    display/settings-page read these days (the "Primary" badge) — actual
    call placement uses get_rotating_numbers instead, which cycles through
    every number an org has, not just this one. Re-prefixes the digits-only
    storage with "+" to match what the provider APIs expect, same as the old
    direct Org.plivo_phone_number/twilio_phone_number reads."""
    rows = (
        await db.execute(
            select(OrgPhoneNumber.provider, OrgPhoneNumber.phone_number).where(
                OrgPhoneNumber.org_id == org_id, OrgPhoneNumber.is_default.is_(True)
            )
        )
    ).all()
    by_provider = {provider: number for provider, number in rows}
    plivo = by_provider.get("plivo")
    twilio = by_provider.get("twilio")
    return (f"+{plivo}" if plivo else None, f"+{twilio}" if twilio else None)


async def get_numbers_by_org(
    db: AsyncSession, org_ids: Iterable[UUID | str]
) -> dict[UUID, dict[str, list[str]]]:
    """Bulk ``{org_id: {provider: [digits-only numbers, ordered]}}`` for a
    batch of orgs at once. Ordered by `position` (see db/models/
    org_phone_number.py for why not `created_at`) — the sequence
    get_rotating_numbers / next_rotating_number rotate through. Used by
    workers/campaign_dialer.py so a claimed batch of targets (almost always
    one or a handful of distinct orgs) costs one extra query total instead
    of a per-org lookup — see that module's docstring for why it can't just
    call get_rotating_numbers per row.
    """
    org_ids = list(org_ids)
    if not org_ids:
        return {}
    rows = (
        await db.execute(
            select(OrgPhoneNumber.org_id, OrgPhoneNumber.provider, OrgPhoneNumber.phone_number)
            .where(OrgPhoneNumber.org_id.in_(org_ids))
            .order_by(OrgPhoneNumber.org_id, OrgPhoneNumber.position, OrgPhoneNumber.id)
        )
    ).all()
    result: dict[UUID, dict[str, list[str]]] = {}
    for org_id, provider, number in rows:
        result.setdefault(org_id, {}).setdefault(provider, []).append(number)
    return result


async def next_rotating_number(
    redis: aioredis.Redis, org_id: UUID | str, provider: str, numbers: list[str]
) -> str | None:
    """The E.164 number this call should dial from, given an already-fetched
    ordered ``numbers`` list (digits-only) for one org+provider — the shared
    building block behind get_rotating_numbers and campaign_dialer's batch
    equivalent. A single number never touches Redis (behaves exactly like
    the old always-use-the-default behavior); 2+ numbers INCR a shared
    per-(org, provider) counter and take it modulo the current list length,
    same pattern as core/tools.py's escalation round robin.
    """
    if not numbers:
        return None
    if len(numbers) == 1:
        return f"+{numbers[0]}"
    turn = await redis.incr(f"{_PHONE_ROUND_ROBIN_PREFIX}{org_id}:{provider}")
    return f"+{numbers[(turn - 1) % len(numbers)]}"


async def get_rotating_numbers(
    db: AsyncSession, redis: aioredis.Redis, org_id: UUID | str
) -> tuple[str | None, str | None]:
    """(plivo_from_e164, twilio_from_e164) for THIS outbound call, round-
    robining across every number the org has configured per provider instead
    of always the single is_default one — call 1 dials from the first
    number (lowest `position`), call 2 from the second, back to the first on
    call 3, and so on. ("Primary"/is_default is a separate, cosmetic-only
    flag — see db/models/org_phone_number.py.) An org with a single number
    per provider behaves exactly as before, since rotation trivially always
    resolves to that number.

    Every outbound-call-placing entry point (routers/admin.py::outbound_call,
    workers/follow_up_dispatcher.py, core/tools.py's AI callback tool) shares
    this one function and so one counter per (org, provider) — the sequence
    holds across the whole org, not per entry point. workers/
    campaign_dialer.py is the exception: see get_numbers_by_org/
    next_rotating_number for its own batched equivalent of this.
    """
    rows = (
        await db.execute(
            select(OrgPhoneNumber.provider, OrgPhoneNumber.phone_number)
            .where(OrgPhoneNumber.org_id == org_id)
            .order_by(OrgPhoneNumber.position, OrgPhoneNumber.id)
        )
    ).all()
    by_provider: dict[str, list[str]] = {}
    for provider, number in rows:
        by_provider.setdefault(provider, []).append(number)

    plivo_from = await next_rotating_number(redis, org_id, "plivo", by_provider.get("plivo", []))
    twilio_from = await next_rotating_number(redis, org_id, "twilio", by_provider.get("twilio", []))
    return plivo_from, twilio_from


async def replace_org_phone_numbers(
    db: AsyncSession, org_id: UUID | str, numbers: list[OrgPhoneNumberIn]
) -> None:
    """Stage a full replace of this org's number rows onto `db` — deletes its
    existing rows and re-adds `numbers`. Caller commits (and catches
    IntegrityError for a 409 when a number is already owned by another org),
    same as every other field on these endpoints.

    Exactly one row per provider ends up is_default=True: the first entry
    for that provider explicitly marked is_default, or — if none are — the
    first entry for that provider in submission order. Each entry's
    `position` is separately set to its 0-based index within that provider's
    own sub-list, in submission order — the sequence get_rotating_numbers
    round-robins outbound calls through (see db/models/org_phone_number.py).
    """
    await db.execute(delete(OrgPhoneNumber).where(OrgPhoneNumber.org_id == org_id))

    default_index: dict[str, int] = {}
    for i, entry in enumerate(numbers):
        if entry.is_default and entry.provider not in default_index:
            default_index[entry.provider] = i
    for i, entry in enumerate(numbers):
        default_index.setdefault(entry.provider, i)

    provider_position: dict[str, int] = {}
    for i, entry in enumerate(numbers):
        position = provider_position.get(entry.provider, 0)
        provider_position[entry.provider] = position + 1
        db.add(
            OrgPhoneNumber(
                org_id=org_id,
                provider=entry.provider,
                phone_number=re.sub(r"\D", "", entry.phone_number),
                is_default=default_index.get(entry.provider) == i,
                position=position,
            )
        )
