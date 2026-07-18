from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import redis.asyncio as aioredis

from apps.api.config import settings

_redis_pool: aioredis.Redis | None = None

# Daily error counter read by GET /admin/stats (error_count_today) and GET
# /admin/reports/timeseries. Key is UTC-dated so it lines up with the UTC day
# boundaries used everywhere else in the reports queries.
ERROR_COUNTER_KEY_FMT = "veerox:errors:{date}"
_ERROR_COUNTER_TTL_SECS = 60 * 60 * 24 * 8  # ~8 days — comfortably outlives any reports window


def get_redis_pool() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def close_redis_pool() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    yield get_redis_pool()


async def record_error() -> None:
    """Best-effort increment of today's (UTC) error counter.

    Called from the top-level catch-all in each channel/worker loop so the
    dashboard's "Errors Today" card reflects real turn failures. Never
    raises — a failure to record shouldn't mask the original error that
    triggered this call.
    """
    try:
        redis = get_redis_pool()
        key = ERROR_COUNTER_KEY_FMT.format(date=datetime.now(UTC).date().isoformat())
        await redis.incr(key)
        await redis.expire(key, _ERROR_COUNTER_TTL_SECS)
    except Exception:  # noqa: BLE001
        pass
