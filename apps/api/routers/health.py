from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from apps.api.deps import DbDep, RedisDep

router = APIRouter(tags=["health"])


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route("/ready", methods=["GET", "HEAD"])
async def ready(db: DbDep, redis: RedisDep) -> dict[str, str]:
    db_status = "ok"
    redis_status = "ok"

    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    try:
        await redis.ping()
    except Exception:
        redis_status = "down"

    return {"db": db_status, "redis": redis_status}
