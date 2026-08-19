"""Ramps concurrent /voice/stream connections against one OR MULTIPLE
running loadtest_server.py instances (round-robining across them, to
simulate what a real load balancer does across horizontally-scaled
instances) and reports success/failure/latency at each concurrency level.

Usage:
    python scripts/loadtest_client.py wss://veerox-staging.onrender.com
    python scripts/loadtest_client.py ws://127.0.0.1:8199          # local
    # multiple instances, comma-separated — simulates horizontal scaling:
    python scripts/loadtest_client.py wss://veerox-staging.onrender.com,wss://veerox-2.onrender.com,wss://veerox-3.onrender.com

Only point this at disposable staging deployments (see
loadtest_server.py's module docstring) — never at production.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

DEFAULT_LEVELS = [10, 30, 50, 80, 120, 160, 200, 300, 500, 800, 1200]
PER_CALL_TIMEOUT = 25


async def one_call(base_url: str, idx: int) -> tuple[bool, float, str]:
    """Simulate one call: connect, send Plivo's 'start' event, wait for the
    fake assistant's audio to come back (proof the whole pipeline — session
    open, DB writes, tool plumbing — ran end to end), then disconnect."""
    phone = f"%2B9190000{idx:05d}"
    url = f"{base_url}/voice/stream?from={phone}&call_uuid=loadtest-{idx}&provider=plivo"
    started = time.monotonic()
    try:
        async with asyncio.timeout(PER_CALL_TIMEOUT):
            async with websockets.connect(url, max_size=None) as ws:
                await ws.send(json.dumps({"event": "start", "start": {"streamId": f"s{idx}"}}))
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("event") == "playAudio":
                        return True, time.monotonic() - started, ""
        return False, time.monotonic() - started, "no playAudio received before disconnect"
    except (TimeoutError, asyncio.TimeoutError):
        return False, time.monotonic() - started, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, time.monotonic() - started, f"{type(exc).__name__}: {exc}"


async def run_level(base_urls: list[str], n: int, start_idx: int) -> None:
    results = await asyncio.gather(
        *[one_call(base_urls[i % len(base_urls)], start_idx + i) for i in range(n)]
    )
    oks = [r for r in results if r[0]]
    fails = [r for r in results if not r[0]]
    latencies = sorted(r[1] for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    print(
        f"level={n:4d}  ok={len(oks):4d}  fail={len(fails):4d}  "
        f"p50={p50:5.2f}s  p95={p95:5.2f}s  max={max(latencies) if latencies else 0:5.2f}s"
    )
    if fails:
        by_reason: dict[str, int] = {}
        for _, _, reason in fails:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    x{count:3d}  {reason}")


async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/loadtest_client.py <ws(s)://host>[,<ws(s)://host2>,...]")
        sys.exit(1)
    base_urls = [u.rstrip("/") for u in sys.argv[1].split(",")]
    levels = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else DEFAULT_LEVELS

    if len(base_urls) > 1:
        print(f"Distributing across {len(base_urls)} instances: {base_urls}")

    idx = 0
    for level in levels:
        await run_level(base_urls, level, idx)
        idx += level
        await asyncio.sleep(1)


asyncio.run(main())
