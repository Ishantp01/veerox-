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
