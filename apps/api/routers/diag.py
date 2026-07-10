"""Temporary diagnostic endpoint — probes the outbound OpenAI Realtime WSS
from wherever the backend is running (typically Render). Delete after the
voice-drop bug is diagnosed; nothing else in the app references this file.
"""

from __future__ import annotations

import asyncio
import json
from time import perf_counter

import structlog
import websockets
from fastapi import APIRouter, Header, HTTPException

from apps.api.config import settings

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/diag", tags=["diag"])


@router.get("/latency")
async def latency_probe(x_admin_token: str | None = Header(None)) -> dict:
    """Time each dependency of a WhatsApp turn from inside the deployment.

    Measures Redis (idempotency/kill-switch path), Postgres (fresh-session
    checkout + query, then a second query on the warm connection), and one
    chat completion on the configured model. Temporary, like the realtime
    probe above — delete once the latency investigation is done.
    """
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    from sqlalchemy import text

    from apps.api.core.llm import chat_completion
    from apps.api.db.session import AsyncSessionLocal
    from apps.api.redis_client import get_redis_pool

    timings: dict = {"chat_model": settings.openai_chat_model}

    # Redis round trips — same pool the webhook/agent path uses.
    redis = get_redis_pool()
    t0 = perf_counter()
    await redis.ping()
    timings["redis_ping_ms"] = round((perf_counter() - t0) * 1000)
    t0 = perf_counter()
    await redis.set("veerox:diag:latency", "1", ex=60)
    timings["redis_set_ms"] = round((perf_counter() - t0) * 1000)

    # Postgres — first query pays connection checkout (incl. pool_pre_ping),
    # second shows the per-query cost on a warm connection.
    t0 = perf_counter()
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT 1"))
        timings["db_first_query_ms"] = round((perf_counter() - t0) * 1000)
        t0 = perf_counter()
        await db.execute(text("SELECT 1"))
        timings["db_warm_query_ms"] = round((perf_counter() - t0) * 1000)

    # One real chat completion on the configured model.
    t0 = perf_counter()
    result = await chat_completion(
        [{"role": "user", "content": "Reply with the single word: ok"}]
    )
    timings["llm_ms"] = round((perf_counter() - t0) * 1000)
    timings["llm_reply"] = (result.content or "")[:40]

    # Per-stage timings of the most recent real WhatsApp turns, recorded by
    # the adapter (see _record_turn_timings there).
    raw_turns = await redis.lrange("veerox:diag:wa_timings", 0, 9)  # type: ignore[misc]
    recent_turns = [json.loads(t) for t in raw_turns]

    return {"ok": True, "timings": timings, "recent_turns": recent_turns}


@router.get("/openai-realtime")
async def openai_realtime_probe(x_admin_token: str | None = Header(None)) -> dict:
    """Reproduce the exact outbound WSS the voice bridge does.

    Returns each step's timing so we can tell whether the hang (or drop) is in
    the OpenAI connect, the session.update handshake, or somewhere after.
    """
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")

    url = f"wss://api.openai.com/v1/realtime?model={settings.openai_realtime_model}"
    headers = {"Authorization": f"Bearer {settings.openai_api_key or ''}"}

    trace: list[dict] = []
    t0 = perf_counter()

    try:
        # Step 1: outbound WSS handshake
        t_before_connect = perf_counter() - t0
        async with await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers, max_size=None),
            timeout=10.0,
        ) as oai:
            trace.append({"step": "connect", "t_ms": round((perf_counter() - t0) * 1000)})

            # Step 2: read session.created
            raw = await asyncio.wait_for(oai.recv(), timeout=5.0)
            trace.append(
                {
                    "step": "session.created",
                    "t_ms": round((perf_counter() - t0) * 1000),
                    "first_event_type": json.loads(raw).get("type"),
                }
            )

            # Step 3: send session.update (same shape as voice bridge)
            session_update = {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": "diagnostic probe",
                    "audio": {
                        "input": {"format": {"type": "audio/pcmu"}},
                        "output": {
                            "format": {"type": "audio/pcmu"},
                            "voice": settings.openai_realtime_voice,
                        },
                    },
                    "tools": [],
                    "tool_choice": "auto",
                },
            }
            await oai.send(json.dumps(session_update))
            trace.append(
                {"step": "session.update.sent", "t_ms": round((perf_counter() - t0) * 1000)}
            )

            # Step 4: read reply (session.updated or error)
            raw2 = await asyncio.wait_for(oai.recv(), timeout=5.0)
            evt2 = json.loads(raw2)
            trace.append(
                {
                    "step": "session.update.reply",
                    "t_ms": round((perf_counter() - t0) * 1000),
                    "event_type": evt2.get("type"),
                    "error": evt2.get("error") if evt2.get("type") == "error" else None,
                }
            )

        trace.append({"step": "closed", "t_ms": round((perf_counter() - t0) * 1000)})
        return {"ok": True, "trace": trace}

    except TimeoutError:
        return {"ok": False, "error": "TimeoutError (10s)", "trace": trace}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": trace,
        }
