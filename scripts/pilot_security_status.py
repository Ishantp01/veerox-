"""Read only pilot replay-state health; never print keys, owners, or credentials."""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def status():
    from apps.api.channels.voice.signature import state_key
    from apps.api.config import settings
    from apps.api.pilot_checks import parse_env
    from apps.api.redis_client import get_redis_pool

    def target(url):
        p = urlsplit(url)
        return p.hostname, p.port or 6379, p.path or "/0"

    main = parse_env((ROOT / ".env").read_text(encoding="utf8"))
    pilot_file = parse_env((ROOT / ".env.pilot").read_text(encoding="utf8"))
    if settings.environment != "pilot" or target(settings.redis_url) == target(main.get("REDIS_URL", "")):
        raise RuntimeError("isolated_pilot_redis_required")
    if target(settings.redis_url) != target(pilot_file.get("REDIS_URL", "")):
        raise RuntimeError("shell_or_other_override_of_pilot_file")
    redis = get_redis_pool()
    try:
        nonce, streams = state_key("nonces"), state_key("streams")
        nonce_ready = bool(await redis.sismember(nonce, "__ready__"))
        stream_ready = await redis.hget(streams, "__ready__") == "1"
        return {
            # Outbound answers use single-use tickets, so a pilot that only dials out needs the stream state alone.
            "outbound_call_ready": stream_ready,
            "connected": bool(await redis.ping()),
            "nonce_state_ready": bool(await redis.sismember(nonce, "__ready__")),
            "stream_state_ready": await redis.hget(streams, "__ready__") == "1",
            "nonce_state_ttl": await redis.ttl(nonce),
            "stream_state_ttl": await redis.ttl(streams),
            "nonce_entries_including_marker": await redis.scard(nonce),
            "stream_entries_including_marker": await redis.hlen(streams),
        }
    finally:
        await redis.aclose()


def main():
    os.environ["VEEROX_ENV_FILE"] = str(ROOT / ".env.pilot")
    logging.disable(logging.CRITICAL)
    try:
        print(json.dumps(asyncio.run(status()), indent=2))
        return 0
    except Exception:
        print('{"status":"BLOCKED","reason":"read_only_security_status_unavailable"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
