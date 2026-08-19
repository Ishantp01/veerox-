"""Bursts signed WhatsApp webhook payloads at a target requests/minute rate
against a running loadtest_whatsapp_server.py instance, measuring webhook
ACK latency and success rate.

Usage:
    python scripts/loadtest_whatsapp_client.py http://127.0.0.1:8198 10000 30
    (base_url, target requests/minute, duration in seconds)

Only point this at a disposable staging/local instance — never production.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time

import httpx

APP_SECRET = "4313e3569d2be9c15acdd2d51703bc87"
PHONE_NUMBER_ID = "1235438426314502"


def _sign(body: bytes) -> str:
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _build_payload(idx: int) -> bytes:
    phone = f"9190000{idx:05d}"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1548329423657131",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "912269986006",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [{"profile": {"name": "Load Test"}, "wa_id": phone}],
                            "messages": [
                                {
                                    "from": phone,
                                    "id": f"wamid.LOADTEST{idx}{int(time.time() * 1000)}",
                                    "timestamp": str(int(time.time())),
                                    "text": {"body": "Hi, what are your prices?"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    return json.dumps(payload).encode()


async def send_one(client: httpx.AsyncClient, url: str, idx: int) -> tuple[bool, float]:
    body = _build_payload(idx)
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}
    started = time.monotonic()
    try:
        resp = await client.post(url, content=body, headers=headers, timeout=15)
        return resp.status_code == 200, time.monotonic() - started
    except Exception:  # noqa: BLE001
        return False, time.monotonic() - started


async def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: python scripts/loadtest_whatsapp_client.py <base_url> <req_per_minute> <duration_secs>")
        sys.exit(1)
    base_url = sys.argv[1].rstrip("/")
    target_rpm = int(sys.argv[2])
    duration = int(sys.argv[3])
    url = f"{base_url}/webhook/whatsapp"

    interval = 60.0 / target_rpm
    print(f"Target: {target_rpm} req/min ({1/interval:.1f}/sec), duration {duration}s, interval {interval*1000:.1f}ms")

    results: list[tuple[bool, float]] = []
    idx = 0
    # httpx.AsyncClient defaults to a ~100-connection pool — with hundreds
    # of requests in flight at once, that queues requests waiting for a
    # free connection and produces exactly the "throttled actual rate, high
    # latency" pattern that looks like a server bottleneck but isn't one.
    limits = httpx.Limits(max_connections=2000, max_keepalive_connections=2000)
    async with httpx.AsyncClient(limits=limits) as client:
        start = time.monotonic()
        tasks = []
        while time.monotonic() - start < duration:
            tasks.append(asyncio.create_task(send_one(client, url, idx)))
            idx += 1
            await asyncio.sleep(interval)
        results = await asyncio.gather(*tasks)

    oks = [r for r in results if r[0]]
    fails = [r for r in results if not r[0]]
    latencies = sorted(r[1] for r in results)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    actual_rpm = len(results) / duration * 60
    print(
        f"sent={len(results)}  ok={len(oks)}  fail={len(fails)}  "
        f"actual_rate={actual_rpm:.0f}/min  p50={p50*1000:.0f}ms  p95={p95*1000:.0f}ms  max={max(latencies)*1000 if latencies else 0:.0f}ms"
    )


asyncio.run(main())
