"""Runs the real app with OpenAI Realtime and ElevenLabs connections faked
out, so a load test exercises the real code path — DB writes, session
handling, audio pump tasks, tool dispatch — without spending real money or
hitting real provider rate limits.

    NEVER set this as a production service's start command. It is a
    standalone entry point, never imported by apps/api/main.py or any
    normal request path — the only way it runs is if something explicitly
    invokes THIS file instead of `uvicorn apps.api.main:app`. Point ONLY a
    disposable staging service (its own Render service, its own database)
    at this script. See longrunning/operations/load.md.

Usage (as a Render "Start Command", or locally):
    python scripts/loadtest_server.py
Respects the same PORT env var Render sets; defaults to 8199 locally.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import structlog
import uvicorn
import websockets as websockets_module

logger = structlog.get_logger(__name__)


class _FakeOpenAIWS:
    """Simulates just enough of the OpenAI Realtime protocol: accepts
    session.update, then emits a short canned text response."""

    def __init__(self) -> None:
        self._out_queue: asyncio.Queue = asyncio.Queue()

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        if msg.get("type") == "response.create":
            await self._out_queue.put(json.dumps({"type": "response.created"}))
            for chunk in ["Hello", " and", " welcome", " to", " Veerox."]:
                await self._out_queue.put(
                    json.dumps({"type": "response.text.delta", "delta": chunk})
                )
                await asyncio.sleep(0.01)
            await self._out_queue.put(
                json.dumps(
                    {"type": "response.text.done", "text": "Hello and welcome to Veerox."}
                )
            )
            await self._out_queue.put(json.dumps({"type": "response.done"}))

    async def close(self) -> None:
        await self._out_queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self._out_queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeElevenLabsWS:
    """Simulates ElevenLabs' streaming-input protocol: one small fake audio
    chunk per turn, then isFinal."""

    def __init__(self) -> None:
        self._sent_text = False

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        if msg.get("text") == "":
            self._sent_text = True

    async def close(self) -> None:
        pass

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self._sent_text:
            self._sent_text = False
            await asyncio.sleep(0.01)
            return json.dumps({"audio": "ZmFrZWF1ZGlv", "isFinal": True})
        await asyncio.sleep(0.05)
        return json.dumps({"audio": "ZmFrZWF1ZGlv", "isFinal": False})


class _FakeConnectResult:
    """Stands in for whatever `websockets.connect(...)` returns — real
    websockets supports BOTH `await websockets.connect(url)` (used in
    elevenlabs_client.py) AND `async with websockets.connect(url) as ws:`
    (used in realtime_bridge.py) on the exact same call. Both call sites
    import the same `websockets` module object, so patching `.connect`
    once here covers both without clobbering."""

    def __init__(self, ws) -> None:
        self._ws = ws

    def __await__(self):
        async def _get():
            return self._ws

        return _get().__await__()

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc) -> None:
        await self._ws.close()


def _fake_connect(url: str, **kwargs):
    if "elevenlabs.io" in url:
        return _FakeConnectResult(_FakeElevenLabsWS())
    return _FakeConnectResult(_FakeOpenAIWS())


def main() -> None:
    logger.warning(
        "loadtest_server_starting_with_fake_providers",
        warning=(
            "OpenAI Realtime and ElevenLabs are FAKED — real calls will get a "
            "canned response, not the real AI. This must only ever run on a "
            "disposable staging service, never on production."
        ),
    )
    websockets_module.connect = _fake_connect

    from apps.api.main import app  # noqa: E402  (patch must land before this import)

    port = int(os.environ.get("PORT", "8199"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
