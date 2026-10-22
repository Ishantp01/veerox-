"""Explicit one-time pilot replay-state initialization.

Two forms. ``--streams-only`` creates just the stream-ownership state. Outbound-call answers are authorised by single-use
tickets minted at dial time, so the nonce ledger is not needed for them and stays uninitialised (every route that still
depends on it keeps refusing, which is the safe state for a pilot that dials out only). The default form also creates the
nonce ledger, which is only safe after a new signing epoch (see the confirmations below).

Never runs on import. This writes only the pilot Redis selected by .env.pilot.
Do not execute until the operator has approved initialization and rotated every
accepted Plivo signing token and waited for all old stream tokens to expire.
Never clear old ledgers
while their signing credentials are accepted. This does not rotate anything.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INITIALIZE_STREAMS = """-- plivo_initialize_streams_v2
if redis.call('EXISTS', KEYS[1]) ~= 0 then return 0 end
redis.call('HSET', KEYS[1], '__ready__', '1')
return 1
"""

INITIALIZE = """-- plivo_initialize_v2
if redis.call('EXISTS', KEYS[1]) + redis.call('EXISTS', KEYS[2]) ~= 0 then return 0 end
redis.call('SADD', KEYS[1], '__ready__')
redis.call('HSET', KEYS[2], '__ready__', '1')
return 1
"""


def redis_target(url):
    from urllib.parse import urlsplit

    parsed = urlsplit(url or "")
    return parsed.hostname, parsed.port or 6379, parsed.path or "/0"


# Values that decide WHICH security state is written. A shell variable of the same name silently beats the env file,
# so each must equal what .env.pilot says; otherwise the script could initialise some other Redis or namespace.
AUTHORITATIVE = ("ENVIRONMENT", "REDIS_URL", "SECRET_ENCRYPTION_KEY", "ADMIN_TOKEN", "PLIVO_AUTH_TOKEN")


def verify_pilot_authority(settings, pilot_env, main_env):
    """Refuse unless the settings in force are exactly the pilot file's and its Redis is not the main environment's."""
    for name in AUTHORITATIVE:
        in_file = (pilot_env.get(name) or "").strip()
        in_force = str(getattr(settings, name.lower(), "") or "").strip()
        if name in ("SECRET_ENCRYPTION_KEY", "ADMIN_TOKEN", "PLIVO_AUTH_TOKEN") and not in_file:
            continue  # not set in the file: nothing to compare (the required-value checks below still apply)
        if in_file != in_force:
            raise RuntimeError("shell_or_other_override_of_pilot_file")
    if not pilot_env.get("REDIS_URL"):
        raise RuntimeError("pilot_redis_missing_from_pilot_file")
    if redis_target(settings.redis_url) == redis_target(main_env.get("REDIS_URL", "")):
        raise RuntimeError("isolated_pilot_redis_required")


async def initialize(streams_only=False):
    from apps.api.channels.voice.signature import state_key
    from apps.api.config import settings
    from apps.api.pilot_checks import parse_env
    from apps.api.redis_client import get_redis_pool

    main = parse_env((ROOT / ".env").read_text(encoding="utf8"))
    pilot_file = parse_env((ROOT / ".env.pilot").read_text(encoding="utf8"))
    if (
        settings.environment != "pilot"
        or not settings.plivo_auth_token
        or not settings.secret_encryption_key
    ):
        raise RuntimeError("pilot_signing_configuration_required")
    verify_pilot_authority(settings, pilot_file, main)
    redis = get_redis_pool()
    try:
        if streams_only:
            result = await redis.eval(INITIALIZE_STREAMS, 1, state_key("streams"))
        else:
            result = await redis.eval(INITIALIZE, 2, state_key("nonces"), state_key("streams"))
        print(
            "Initialized new pilot security epoch."
            if result == 1
            else "Refused: security state already exists."
        )
        return 0 if result == 1 else 1
    finally:
        await redis.aclose()


def required_confirmations(streams_only):
    """The attestations each form needs. The rotation one protects the nonce ledger only."""
    common = ["confirm_all_old_stream_tokens_expired", "confirm_all_pilot_workers_stopped"]
    return common if streams_only else ["confirm_all_plivo_signing_tokens_rotated", *common]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams-only", action="store_true")
    parser.add_argument("--confirm-all-plivo-signing-tokens-rotated", action="store_true")
    parser.add_argument("--confirm-all-old-stream-tokens-expired", action="store_true")
    parser.add_argument("--confirm-all-pilot-workers-stopped", action="store_true")
    args = parser.parse_args(argv)
    missing = [name for name in required_confirmations(args.streams_only) if not getattr(args, name)]
    if missing:
        parser.error("required confirmations missing: " + ", ".join("--" + n.replace("_", "-") for n in missing))
    os.environ["VEEROX_ENV_FILE"] = str(ROOT / ".env.pilot")
    import logging

    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(initialize(args.streams_only))
    except Exception:
        print("Initialization failed closed; no diagnostic values printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
