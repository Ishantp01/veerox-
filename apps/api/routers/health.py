from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.deps import DbDep, RedisDep

router = APIRouter(tags=["health"])


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route("/ready", methods=["GET", "HEAD"])
async def ready(db: DbDep, redis: RedisDep) -> JSONResponse:
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

    healthy = db_status == "ok" and redis_status == "ok"
    return JSONResponse({"db": db_status, "redis": redis_status}, status_code=200 if healthy else 503)
