"""Server-side dashboard login sessions, stored in Redis.

Chosen over JWTs specifically for instant revocation: rotating an org
admin's login token or a payment lapsing needs to kill access immediately,
not wait out a token's expiry. Redis is already a dependency
(apps/api/redis_client.py), so this adds no new infra.
"""

from __future__ import annotations

import json
import secrets
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from apps.api.config import settings

_SESSION_KEY_FMT = "session:{token}"
# Secondary index so a single token rotation can invalidate every session
# that user holds, instead of waiting for TTL expiry per-token.
_USER_SESSIONS_KEY_FMT = "user_sessions:{account_user_id}"


async def create_session(
    redis: aioredis.Redis, *, account_user_id: UUID, org_id: UUID, role: str
) -> str:
    token = secrets.token_urlsafe(32)
    payload = {
        "account_user_id": str(account_user_id),
        "org_id": str(org_id),
        "role": role,
    }
    session_key = _SESSION_KEY_FMT.format(token=token)
    # Pipelined into one round trip — SET and SADD don't depend on each
    # other's result, and Redis here is a remote Upstash instance (see
    # .env), so batching avoids paying network latency twice.
    async with redis.pipeline(transaction=False) as pipe:
        pipe.set(session_key, json.dumps(payload), ex=settings.session_ttl_seconds)
        pipe.sadd(_USER_SESSIONS_KEY_FMT.format(account_user_id=account_user_id), token)  # type: ignore[misc]
        await pipe.execute()
    return token


async def get_session(redis: aioredis.Redis, token: str) -> dict[str, Any] | None:
    raw = await redis.get(_SESSION_KEY_FMT.format(token=token))
    if raw is None:
        return None
    result: dict[str, Any] = json.loads(raw)
    return result


async def delete_session(redis: aioredis.Redis, token: str) -> None:
    payload = await get_session(redis, token)
    await redis.delete(_SESSION_KEY_FMT.format(token=token))
    if payload is not None:
        await redis.srem(  # type: ignore[misc]
            _USER_SESSIONS_KEY_FMT.format(account_user_id=payload["account_user_id"]), token
        )


async def invalidate_user_sessions(redis: aioredis.Redis, account_user_id: UUID) -> None:
    """Kill every active session for a user — used when an admin's login
    token is regenerated (see billing.py's regenerate_admin_token), so the
    old token stops working immediately rather than at TTL expiry.
    """
    index_key = _USER_SESSIONS_KEY_FMT.format(account_user_id=account_user_id)
    tokens = await redis.smembers(index_key)  # type: ignore[misc]
    if tokens:
        await redis.delete(*(_SESSION_KEY_FMT.format(token=t) for t in tokens))
    await redis.delete(index_key)
