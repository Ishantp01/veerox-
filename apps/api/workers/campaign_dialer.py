"""In-process background dialer for outbound calling campaigns.

Started as an ``asyncio.create_task`` from the FastAPI lifespan (see
``apps/api/main.py``) rather than a separate job-queue process — throughput
is bounded by ``settings.max_concurrent_calls`` in-flight calls at a time
(the Plivo account's own concurrent-call cap is the ceiling on that number),
not by queue overhead, so a simple poll loop is enough. Durability across
restarts is handled by requeuing any target stuck ``calling`` on startup
rather than by a durable job broker.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import func, select, update

from apps.api.channels.voice import failover as voice_failover
from apps.api.channels.voice.org_numbers import get_numbers_by_org, next_rotating_number
from apps.api.channels.voice.realtime_bridge import start_precall_connect
from apps.api.config import settings
from apps.api.core.agent import _is_kill_switch_active
from apps.api.core.usage import get_credit_usage
from apps.api.db.models.call_campaign import CallCampaign
from apps.api.db.models.campaign_target import CampaignTarget
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.session import AsyncSessionLocal
from apps.api.deps import is_over_plan_limit
from apps.api.redis_client import get_redis_pool, record_error
from apps.api.workers.campaign_scheduling import (
    complete_finished_campaigns,
    promote_scheduled_campaigns,
)

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECS = 5
# Fallback dial-attempt cap, used only when a target somehow has no owning
# campaign row to read ``CallCampaign.max_attempts`` off (shouldn't happen —
# a FK enforces it). Each campaign carries its own 1-5 value, chosen at
# creation time; this is just the historical default.
_MAX_ATTEMPTS = 3
# Backstop only — Plivo's hangup_url callback (handle_call_ended, below) is
# what normally releases a finished call within seconds. This timeout only
# matters if that callback never arrives (network hiccup, Plivo outage).
# Generous enough not to cut off a real, still-in-progress conversation.
_STALE_CALL_TIMEOUT_SECS = 300


async def _requeue_stuck_targets() -> None:
    """Requeue targets left ``calling`` by a previous process (crash/restart
    mid-call) so a campaign never stalls forever on one dropped call.

    Scoped to voice targets only — ``CampaignTarget.status`` values are
    shared with the WhatsApp dispatcher (apps/api/workers/
    whatsapp_dispatcher.py), which has its own requeue-on-startup pass.

    ``attempt_count`` was already incremented when the interrupted call was
    staged, so a target that has now hit its campaign's ``max_attempts`` is
    marked ``failed`` rather than requeued — the total number of calls placed
    to any one lead never exceeds the campaign's chosen "Call attempts".
    """
    async with AsyncSessionLocal() as db:
        max_attempts_sq = (
            select(CallCampaign.max_attempts)
            .where(CallCampaign.id == CampaignTarget.campaign_id)
            .scalar_subquery()
        )
        base = (
            CampaignTarget.status == "calling",
            CampaignTarget.channel == "voice",
        )
        requeued = await db.execute(
            update(CampaignTarget)
            .where(*base, CampaignTarget.attempt_count < max_attempts_sq)
            .values(status="pending")
        )
        exhausted = await db.execute(
            update(CampaignTarget)
            .where(*base, CampaignTarget.attempt_count >= max_attempts_sq)
            .values(status="failed")
        )
        await db.commit()
        if requeued.rowcount or exhausted.rowcount:
            logger.info(
                "campaign_dialer_requeued_stuck_targets",
                count=requeued.rowcount,
                failed_at_cap=exhausted.rowcount,
            )


async def _reclaim_stale_calls(db) -> None:
    """Requeue/fail any voice target that's been ``calling`` past the timeout.

    Runs every tick so a bad number or a call that never connects doesn't
    permanently wedge the sequential dialer — no restart required. Scoped to
    voice targets; see ``_requeue_stuck_targets`` for why.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=_STALE_CALL_TIMEOUT_SECS)
    stmt = (
        select(CampaignTarget, CallCampaign.max_attempts)
        .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
        .where(
            CampaignTarget.status == "calling",
            CampaignTarget.called_at < cutoff,
            CampaignTarget.channel == "voice",
        )
    )
    stale = (await db.execute(stmt)).all()
    for target, max_attempts in stale:
        if target.conversation_id is not None:
            target.status = "failed"
        else:
            target.status = "pending" if target.attempt_count < max_attempts else "failed"
        logger.warning(
            "campaign_dialer_reclaimed_stale_call",
            target_id=str(target.id),
            new_status=target.status,
        )
    if stale:
        await db.commit()


async def _fail_capped_targets(db) -> None:
    """Flip any ``pending`` voice target that has already used all of its
    campaign's ``max_attempts`` to ``failed``.

    The dialer's claim query skips these, so without this they'd sit
    ``pending`` forever and keep a finished campaign from ever completing.
    Normally an outcome handler already marks the last attempt ``failed`` —
    this only catches targets a crash or edge case left ``pending`` at the
    cap.
    """
    max_attempts_sq = (
        select(CallCampaign.max_attempts)
        .where(CallCampaign.id == CampaignTarget.campaign_id)
        .scalar_subquery()
    )
    result = await db.execute(
        update(CampaignTarget)
        .where(
            CampaignTarget.status == "pending",
            CampaignTarget.channel == "voice",
            CampaignTarget.attempt_count >= max_attempts_sq,
        )
        .values(status="failed")
    )
    if result.rowcount:
        await db.commit()
        logger.info("campaign_dialer_failed_capped_targets", count=result.rowcount)


async def _count_calls_in_flight(db) -> int:
    """How many voice calls are currently in progress — bounds concurrency.

    Scoped to voice targets so in-progress WhatsApp conversations (which
    share the "calling" status value) never count against the phone dialer.
    """
    stmt = (
        select(func.count())
        .select_from(CampaignTarget)
        .where(CampaignTarget.status == "calling", CampaignTarget.channel == "voice")
    )
    return (await db.execute(stmt)).scalar_one()


async def _claim_targets() -> list[tuple[str, str, int, str | None, str | None, str | None, UUID, int]]:
    """Atomically claim up to the remaining concurrency budget's worth of the
    oldest pending targets of running campaigns.

    Returns ``[(target_id, phone, attempt_count, plivo_from, twilio_from,
    preferred_provider, org_id, max_attempts), ...]`` as strings/int so the
    caller can place the calls outside this short-lived session.
    ``max_attempts`` is the owning ``CallCampaign.max_attempts`` (1-5), so
    ``_dial_one`` can decide retry-vs-fail without another query.
    ``plivo_from``/``twilio_from``
    round-robin across every dedicated number the owning org has on that
    provider (see ``db/models/org_phone_number.py`` — an org can have
    several per provider), or ``None`` to fall back to that provider's
    platform default. The claim query itself no longer joins those numbers
    in-line (that used to fetch only the ``is_default`` row per provider,
    which doesn't generalize to picking *one of several* per claimed target);
    instead, once this batch's final claimed set is known, one bulk lookup
    (``channels/voice/org_numbers.py::get_numbers_by_org``) fetches every
    number for the (typically one or a handful of) distinct orgs in the
    batch, and each target's from-number is resolved off that in memory —
    still one extra query total, not one per org_numbers.py::
    get_rotating_numbers call. ``preferred_provider`` is the org's explicit
    Plivo/Twilio override (``Org.preferred_voice_provider``), or ``None``
    for automatic ordering — unless the target's campaign has pinned a
    specific ``CallCampaign.phone_number_id``, in which case that number's
    own provider overrides it for this call only, and rotation (including
    the shared per-(org, provider) Redis counter) is bypassed entirely for
    that target, so a pinned campaign never perturbs rotation for the rest
    of the org's pool. Empty if there's nothing to
    dial right now or ``max_concurrent_calls`` voice calls are already in
    flight.

    Targets belonging to an org that's over its plan's
    ``max_call_minutes`` are skipped (left ``pending``, not
    claimed) rather than dialed — this is the only place campaign calls
    actually get placed, so without this check a 0-minute (or exhausted)
    plan would never stop an already-running campaign from dialing.
    """
    async with AsyncSessionLocal() as db:
        await promote_scheduled_campaigns(db)
        await _reclaim_stale_calls(db)
        await _fail_capped_targets(db)
        await complete_finished_campaigns(db)
        capacity = settings.max_concurrent_calls - await _count_calls_in_flight(db)
        if capacity <= 0:
            return []

        # Over-fetch beyond `capacity` since some candidates may belong to an
        # over-limit org and get skipped rather than claimed.
        #
        # with_for_update(skip_locked=True, of=CampaignTarget): without
        # this, two dialer instances (horizontal scaling — see
        # longrunning/operations/load.md) polling at the same moment could
        # both SELECT the same pending targets before either commits its
        # claim, and both place a real call to the same person. Locking
        # just CampaignTarget (not the joined CallCampaign/Org rows) avoids
        # contending with unrelated admin operations on those tables. A
        # no-op with a single dialer instance — the lock only ever
        # contends against a second instance's own claim attempt.
        stmt = (
            select(
                CampaignTarget,
                CallCampaign.org_id,
                Org.preferred_voice_provider,
                CallCampaign.phone_number_id,
                CallCampaign.max_attempts,
            )
            .join(CallCampaign, CallCampaign.id == CampaignTarget.campaign_id)
            .join(Org, Org.id == CallCampaign.org_id)
            .where(
                CampaignTarget.status == "pending",
                CallCampaign.status == "running",
                CampaignTarget.channel == "voice",
                # Hard ceiling: never place more than the campaign's chosen
                # "Call attempts" calls to one lead, no matter how a prior
                # attempt was left `pending` (outcome-handler edge case,
                # worker restart, ...). Outcome handlers still flip a target
                # to `failed` when its last allowed attempt doesn't connect;
                # this is the backstop that makes the cap absolute.
                CampaignTarget.attempt_count < CallCampaign.max_attempts,
            )
            .order_by(CampaignTarget.created_at)
            .limit(max(capacity * 4, 50))
            .with_for_update(skip_locked=True, of=CampaignTarget)
        )
        rows = (await db.execute(stmt)).all()

        org_over_limit: dict[UUID, bool] = {}
        staged: list[tuple[CampaignTarget, UUID, str | None, UUID | None, int]] = []
        for target, org_id, preferred_provider, phone_number_id, max_attempts in rows:
            if len(staged) >= capacity:
                break
            if org_id not in org_over_limit:
                usage = await get_credit_usage(db, org_id)
                org_over_limit[org_id] = await is_over_plan_limit(
                    db, org_id, "max_call_minutes", usage.call_minutes
                )
            if org_over_limit[org_id]:
                continue
            target.status = "calling"
            target.attempt_count += 1
            target.called_at = datetime.now(UTC)
            staged.append((target, org_id, preferred_provider, phone_number_id, max_attempts))

        if not staged:
            return []

        redis = get_redis_pool()
        numbers_by_org = await get_numbers_by_org(db, {org_id for _, org_id, _, _, _ in staged})

        pinned_ids = {
            phone_number_id for _, _, _, phone_number_id, _ in staged if phone_number_id is not None
        }
        pinned_numbers: dict[UUID, tuple[str, str]] = {}
        if pinned_ids:
            pinned_rows = (
                await db.execute(
                    select(OrgPhoneNumber.id, OrgPhoneNumber.provider, OrgPhoneNumber.phone_number).where(
                        OrgPhoneNumber.id.in_(pinned_ids)
                    )
                )
            ).all()
            pinned_numbers = {pn_id: (provider, number) for pn_id, provider, number in pinned_rows}

        claimed = []
        for target, org_id, preferred_provider, phone_number_id, max_attempts in staged:
            pinned = pinned_numbers.get(phone_number_id) if phone_number_id is not None else None
            if pinned is not None:
                provider, number = pinned
                plivo_from = f"+{number}" if provider == "plivo" else None
                twilio_from = f"+{number}" if provider == "twilio" else None
                effective_provider = provider
            else:
                org_numbers = numbers_by_org.get(org_id, {})
                plivo_from = await next_rotating_number(redis, org_id, "plivo", org_numbers.get("plivo", []))
                twilio_from = await next_rotating_number(
                    redis, org_id, "twilio", org_numbers.get("twilio", [])
                )
                effective_provider = preferred_provider
            claimed.append(
                (
                    str(target.id),
                    target.phone,
                    target.attempt_count,
                    plivo_from,
                    twilio_from,
                    effective_provider,
                    org_id,
                    max_attempts,
                )
            )
        await db.commit()
        return claimed


async def _mark_target(target_id: str, status: str) -> None:
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, target_id)
        if target is not None:
            target.status = status
            await db.commit()


async def handle_call_ended(target_id: str) -> None:
    """Free up a concurrency slot as soon as Plivo reports a call is over.

    Wired as the ``hangup_url`` on the outbound call (see ``_dial_batch``) so
    a no-answer/busy/dropped call frees up the dialer within seconds instead
    of waiting on the ``_STALE_CALL_TIMEOUT_SECS`` backstop. No-ops if the
    call already completed via ``qualify_lead`` (status is no longer
    ``"calling"`` by the time this fires).

    Only retries (``pending``) when the call never connected at all — a
    ``conversation_id`` on the target (set by ``voice_adapter.
    attach_campaign_conversation`` the moment the audio bridge connects) is
    proof the prospect actually answered and talked, so that always resolves
    to ``failed`` instead: re-dialing someone who already picked up just
    because the AI didn't call qualify_lead in time would be wrong, no
    matter how many attempts are left.
    """
    async with AsyncSessionLocal() as db:
        target = await db.get(CampaignTarget, UUID(target_id))
        if target is None or target.status != "calling":
            return
        if target.conversation_id is not None:
            target.status = "failed"
        else:
            campaign = await db.get(CallCampaign, target.campaign_id)
            max_attempts = campaign.max_attempts if campaign is not None else _MAX_ATTEMPTS
            target.status = "pending" if target.attempt_count < max_attempts else "failed"
        await db.commit()
        logger.info("campaign_dialer_call_ended", target_id=target_id, new_status=target.status)


async def _dial_one(
    target_id: str,
    phone: str,
    attempt_count: int,
    plivo_from: str | None,
    twilio_from: str | None,
    preferred_provider: str | None,
    org_id: UUID,
    max_attempts: int,
) -> None:
    answer_url = (
        f"{settings.public_base_url.rstrip('/')}/voice/answer?campaign_target_id={target_id}"
    )
    hangup_url = (
        f"{settings.public_base_url.rstrip('/')}/voice/campaign-hangup"
        f"?campaign_target_id={target_id}"
    )

    if not voice_failover.is_configured():
        # Local-dev fallback, same convention as POST /admin/outbound/call:
        # leave the target "calling" (simulating a placed call) rather than
        # failing it outright, so the dialer's concurrency gate and the
        # stuck-target requeue-on-restart path stay testable without real
        # Plivo credentials.
        logger.warning("campaign_dialer_plivo_not_configured", target_id=target_id)
        return

    try:
        result, provider = await voice_failover.initiate_call(
            phone,
            answer_url,
            hangup_url=hangup_url,
            plivo_from_number=plivo_from,
            twilio_from_number=twilio_from,
            preferred_provider=preferred_provider,
        )
        # Only worth flagging when Twilio wasn't this org's own dedicated
        # provider or explicit preference (i.e. it wasn't picked on purpose)
        # — see failover.initiate_call's provider-ordering docstring.
        if provider == "twilio" and not twilio_from and preferred_provider != "twilio":
            logger.warning("campaign_dialer_fell_back_to_twilio", target_id=target_id)
        request_uuid = result.get("request_uuid") or result.get("sid")
        if isinstance(request_uuid, str):
            # Warm up the OpenAI Realtime session for the whole ring
            # duration instead of waiting for the callee to answer — see
            # start_precall_connect's docstring.
            start_precall_connect(request_uuid, UUID(target_id), org_id)
    except httpx.HTTPError:
        logger.warning("campaign_dialer_initiate_call_failed", target_id=target_id)
        await _mark_target(target_id, "pending" if attempt_count < max_attempts else "failed")


async def _dial_batch() -> None:
    claimed = await _claim_targets()
    if not claimed:
        return
    await asyncio.gather(
        *(
            _dial_one(
                target_id,
                phone,
                attempt_count,
                plivo_from,
                twilio_from,
                preferred_provider,
                org_id,
                max_attempts,
            )
            for (
                target_id,
                phone,
                attempt_count,
                plivo_from,
                twilio_from,
                preferred_provider,
                org_id,
                max_attempts,
            ) in claimed
        )
    )


async def run_campaign_dialer() -> None:
    """The dialer's main loop — runs for the lifetime of the app process."""
    await _requeue_stuck_targets()
    while True:
        try:
            if not await _is_kill_switch_active():
                await _dial_batch()
        except Exception:  # noqa: BLE001
            logger.exception("campaign_dialer_tick_failed")
            await record_error()
        await asyncio.sleep(_POLL_INTERVAL_SECS)
