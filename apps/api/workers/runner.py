"""Starts the in-process background workers; shared by the API (when RUN_BACKGROUND_WORKERS is on) and the
dedicated worker container (apps/api/worker_main.py)."""

from __future__ import annotations

import asyncio

import structlog

from apps.api import pilot
from apps.api.channels.voice.plivo_provisioning import register_all_org_plivo_numbers
from apps.api.db.session import AsyncSessionLocal
from apps.api.workers.aws_cost_importer import run_aws_cost_importer
from apps.api.workers.billing_worker import run_late_event_adjustment_sweep
from apps.api.workers.campaign_dialer import run_campaign_dialer
from apps.api.workers.follow_up_dispatcher import run_follow_up_dispatcher
from apps.api.workers.license_expiry_worker import run_license_expiry_worker
from apps.api.workers.usage_daily_aggregator import run_usage_daily_aggregator
from apps.api.workers.usage_monthly_aggregator import run_usage_monthly_aggregator
from apps.api.workers.voice_retention_worker import run_voice_retention_worker
from apps.api.workers.whatsapp_dispatcher import run_whatsapp_dispatcher


async def _register_all_org_plivo_numbers() -> None:
    try:
        async with AsyncSessionLocal() as db:
            await register_all_org_plivo_numbers(db)
    except Exception:  # noqa: BLE001 - best-effort, must never block or crash startup
        pass


def start_background_workers() -> tuple[asyncio.Task[None], ...]:
    """Create the worker tasks (none in pilot mode unless PILOT_WORKERS_ENABLED)."""
    if not pilot.workers_enabled():
        # ENVIRONMENT=pilot without PILOT_WORKERS_ENABLED: no dialer, dispatchers, billing jobs or Plivo
        # re-registration can run against the pilot database with real provider credentials in the environment.
        structlog.get_logger("pilot").warning("pilot_background_workers_disabled")
        return ()
    return (
        asyncio.create_task(_register_all_org_plivo_numbers()),
        asyncio.create_task(run_campaign_dialer()),
        asyncio.create_task(run_whatsapp_dispatcher()),
        asyncio.create_task(run_follow_up_dispatcher()),
        asyncio.create_task(run_license_expiry_worker()),
        asyncio.create_task(run_usage_daily_aggregator()),
        asyncio.create_task(run_usage_monthly_aggregator()),
        asyncio.create_task(run_aws_cost_importer()),
        asyncio.create_task(run_late_event_adjustment_sweep()),
        asyncio.create_task(run_voice_retention_worker()),
    )
