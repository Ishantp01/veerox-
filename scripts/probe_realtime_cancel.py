"""PAID PROBE (not run): does response.cancel close a gpt-realtime session on this model?

    OPENAI_API_KEY=... python scripts/probe_realtime_cancel.py --i-approve-a-paid-openai-call

Opens one Realtime session, asks for a long text answer, sends response.cancel after the first deltas, then
checks (a) response.done arrives with status cancelled, (b) the socket is still open, (c) a second response
completes on the same session. Costs a few cents. Prints a verdict line; changes nothing else.
Use it before enabling VOICE_SAFE_CANCEL anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import websockets

MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")


async def run() -> int:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("OPENAI_API_KEY is not set")
        return 2
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}, max_size=None) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {"type": "realtime", "output_modalities": ["text"], "instructions": "Reply in plain text."}}))

        async def ask(prompt: str) -> None:
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}}))
            await ws.send(json.dumps({"type": "response.create"}))

        await ask("Count slowly from 1 to 200, one number per line.")
        deltas, cancelled_at, done_status, done_at, closed = 0, None, None, None, False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                closed = True
                break
            kind = event.get("type", "")
            if kind.endswith(".delta") and cancelled_at is None:
                deltas += 1
                if deltas == 5:
                    await ws.send(json.dumps({"type": "response.cancel"}))
                    cancelled_at = time.monotonic()
            if kind == "response.done":
                done_status, done_at = event.get("response", {}).get("status"), time.monotonic()
                break
        second_ok = False
        if not closed and done_at:
            await ask("Say OK.")
            try:
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    if event.get("type") == "response.done":
                        second_ok = event.get("response", {}).get("status") == "completed"
                        break
            except (asyncio.TimeoutError, websockets.ConnectionClosed):
                closed = True
    ms = round((done_at - cancelled_at) * 1000) if done_at and cancelled_at else None
    verdict = "PASS" if done_status == "cancelled" and second_ok and not closed else "FAIL"
    print(json.dumps({"verdict": verdict, "model": MODEL, "done_status": done_status, "cancel_to_done_ms": ms, "socket_closed": closed, "second_response_ok": second_ok}))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--i-approve-a-paid-openai-call", action="store_true", required=True)
    parser.parse_args()
    sys.exit(asyncio.run(run()))
