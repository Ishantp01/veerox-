# ruff: noqa: E501  (status lines and long provider messages)
"""Check a stack's voice configuration BEFORE placing a call. Read-only: free provider lookups, nothing is dialled,
synthesised or written, and no secret is printed.

    python scripts\\voice_preflight.py                       # the main .env (the local development stack)
    $env:VEEROX_ENV_FILE=".env.pilot"; python scripts\\voice_preflight.py   # the pilot file

Exit status 1 when something would make a call silent or unsafe (an invalid voice, a missing key, an unreachable or
non-https public URL); warnings do not change the status.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api import config  # noqa: E402
from apps.api.channels.voice import elevenlabs_client  # noqa: E402
from apps.api.channels.voice.voice_profile import platform_model_id, platform_voice_id  # noqa: E402

settings = config.settings
problems: list[str] = []
warnings: list[str] = []


def say(status: str, text: str) -> None:
    print(f"{status:5s} {text}")


def get_json(url: str, headers: dict[str, str]) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except Exception:  # noqa: BLE001
        return 0, {}


async def main() -> int:
    say("INFO", f"env file: {Path(config.ENV_FILE).name} | environment: {settings.environment}")
    if settings.voice_tts_provider != "elevenlabs":
        say("INFO", f"TTS provider is {settings.voice_tts_provider!r}: the ElevenLabs checks are skipped")
    else:
        voice_id = platform_voice_id()
        check = await elevenlabs_client.validate_voice(voice_id)
        shown = f"{voice_id[:6]}...{voice_id[-4:]}"
        if check.ok:
            say("PASS", f"ElevenLabs voice {shown} exists for this key (model {platform_model_id()})")
        elif check.ok is False:
            say("FAIL", f"ElevenLabs voice {shown} is rejected ({check.reason}): every call would be SILENT")
            problems.append("voice")
        else:
            say("WARN", f"could not reach ElevenLabs to check the voice ({check.reason})")
    key = settings.openai_api_key
    if not key:
        say("FAIL", "no OPENAI_API_KEY")
        problems.append("openai")
    else:
        status, body = await asyncio.to_thread(get_json, "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})
        ids = {m["id"] for m in body.get("data", [])}
        for model in (settings.openai_realtime_model, settings.voice_live_transcribe_model):
            if status == 200 and model in ids:
                say("PASS", f"OpenAI model {model} is available to this key")
            elif status == 200:
                say("FAIL", f"OpenAI model {model} is NOT available to this key")
                problems.append(model)
            else:
                say("WARN", f"could not list OpenAI models (HTTP {status})")
    base = settings.public_base_url
    host = urlparse(base).hostname or ""
    if not base.startswith("https://") or host in ("", "example.com") or "example." in host:
        say("FAIL", "PUBLIC_BASE_URL is not a real https URL: the provider cannot reach this stack")
        problems.append("public_base_url")
    else:
        status, _ = await asyncio.to_thread(get_json, base.rstrip("/") + "/health", {"User-Agent": "voice-preflight"})
        say("PASS" if status == 200 else "WARN", f"public URL answers /health with {status or 'no response'} (needs the tunnel and API running)")
    if settings.voice_dial_allowlist.strip() or settings.pilot_allowed_numbers.strip():
        say("PASS", "a dial allowlist is configured")
    else:
        say("WARN", "no dial allowlist: this stack can dial any number")
        warnings.append("allowlist")
    for name, value in (("VOICE_MAX_ACTIVE_STREAMS", settings.voice_max_active_streams), ("VOICE_MAX_CALL_SECONDS", settings.voice_max_call_seconds),
                        ("VOICE_OUTBOUND_TIME_LIMIT_SECONDS", settings.voice_outbound_time_limit_seconds)):
        say("PASS" if value else "WARN", f"{name} = {value or 'not set (no limit)'}")
    print()
    print("RESULT:", "NOT READY - " + ", ".join(problems) if problems else "ready" + (f" (with {len(warnings)} warning(s))" if warnings else ""))
    return 1 if problems else 0


raise SystemExit(asyncio.run(main()))
