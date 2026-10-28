# ruff: noqa: E501  (plan text and status lines)
r"""Controlled samples for the 'same voice sounds different on calls' comparison (stages B and C of the plan).

DRY RUN by default: prints the plan, the exact characters and the credit estimate, and generates nothing.
``--run`` synthesises (PAID) and ``--max-chars`` is a hard cap checked before every request.

    python scripts\voice_consistency_samples.py                       # plan + cost only
    python scripts\voice_consistency_samples.py --run --max-chars 700 # approved run

Every sample uses the SAME short sentence, voice and model; only one thing changes at a time:
  B0  API, voice_settings OMITTED, full band           what the provider does when nothing is sent
  B1  API, the voice's OWN stored settings, full band  what the website sounds like with this model
  B2  API, the settings VEEROX sends, full band        Veerox's settings without the phone format
  B3  API, stored settings, 8 kHz mu-law               the stored settings through the phone format
  C1  Veerox pipeline as it runs today (8 kHz mu-law)  what is handed to Plivo today
  C2  Veerox pipeline with the stored settings         the proposed fix, through the same pipeline
A (the website preview) and D (what you hear on the phone) are recorded by you; see docs/voice-consistency-plan.md.
Output: .validation-voice-consistency/ (git-ignored). Only api.elevenlabs.io is contacted: no calls, no database.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.channels.voice import elevenlabs_client  # noqa: E402
from apps.api.channels.voice.voice_profile import (  # noqa: E402
    platform_model_id,
    platform_voice_id,
    resolve_profile,
)
from apps.api.config import settings  # noqa: E402

TEXTS = {
    "roman": "Namaste, main Veerox ka AI assistant hoon. Kya main aapse do minute baat kar sakta hoon?",
    "devanagari": "नमस्ते, मैं वीरॉक्स का एआई असिस्टेंट हूँ। क्या मैं आपसे दो मिनट बात कर सकता हूँ?",
}


def stored_settings(voice_id: str) -> dict:
    req = urllib.request.Request(f"https://api.elevenlabs.io/v1/voices/{voice_id}/settings", headers={"xi-api-key": settings.elevenlabs_api_key or ""})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return {k: data[k] for k in ("stability", "similarity_boost", "style", "use_speaker_boost", "speed") if k in data}


def veerox_settings(model: str) -> dict:
    sent = dict(elevenlabs_client.LEGACY_VOICE_SETTINGS)
    if model in elevenlabs_client.STYLE_MODELS:
        sent["style"] = elevenlabs_client.DEFAULT_STYLE
    return sent


def rest(voice_id: str, model: str, text: str, fmt: str, voice_settings: dict | None) -> bytes:
    body: dict = {"text": text, "model_id": model}
    if voice_settings is not None:
        body["voice_settings"] = voice_settings
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format={fmt}", json.dumps(body).encode(),
        {"Content-Type": "application/json", "xi-api-key": settings.elevenlabs_api_key or ""})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"provider refused the request: HTTP {exc.code}") from exc


async def pipeline(text: str, profile: dict | None) -> bytes:
    """Exactly what a call does: the real streaming session, text fed as small deltas, mu-law 8 kHz out."""
    chunks: list[bytes] = []
    async with elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile(profile) if profile else None) as session:
        async def collect() -> None:
            async for b64 in session.audio_chunks():
                chunks.append(base64.b64decode(b64))

        reader = asyncio.create_task(collect())
        for piece in text.split(" "):
            await session.send_text(piece + " ")
            await asyncio.sleep(0.025)
        await session.finish()
        await asyncio.wait_for(reader, timeout=30)
        if session.failed:
            raise SystemExit(f"streaming session failed: {session.fail_code}")
    return b"".join(chunks)


def plan(model: str, voice_id: str, stored: dict | None) -> list[dict]:
    stored = stored or {}
    proposed = {"model_id": model, **{k: v for k, v in stored.items()}}
    return [
        {"id": "B0", "kind": "rest", "fmt": "mp3_44100_128", "settings": None},
        {"id": "B1", "kind": "rest", "fmt": "mp3_44100_128", "settings": stored},
        {"id": "B2", "kind": "rest", "fmt": "mp3_44100_128", "settings": veerox_settings(model)},
        {"id": "B3", "kind": "rest", "fmt": "ulaw_8000", "settings": stored},
        {"id": "C1", "kind": "pipeline", "profile": None},
        {"id": "C2", "kind": "pipeline", "profile": proposed},
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", choices=sorted(TEXTS), default="roman", help="which spelling of the one test sentence")
    ap.add_argument("--run", action="store_true", help="synthesise (PAID)")
    ap.add_argument("--max-chars", type=int, default=0, help="hard cap on characters submitted (required with --run)")
    ap.add_argument("--out", default=".validation-voice-consistency")
    args = ap.parse_args()
    text, voice_id, model = TEXTS[args.script], platform_voice_id(), platform_model_id()
    try:
        stored = stored_settings(voice_id)
    except Exception:  # noqa: BLE001
        stored = {}
    jobs = plan(model, voice_id, stored)
    total = len(text) * len(jobs)
    per_char = 1.0 if model == "eleven_multilingual_v2" else 0.5
    print(f"voice {voice_id[:6]}...{voice_id[-4:]} | model {model} | script {args.script} | sentence {len(text)} chars x {len(jobs)} samples = {total} chars")
    print(f"estimated credits: about {total * per_char:.0f} (multilingual_v2 = 1/char, flash/turbo = 0.5-1/char; roughly $0.05-0.10 per 1,000 characters on third-party price lists)")
    print("voice's own stored settings :", stored or "(could not be read)")
    print("settings Veerox sends today :", veerox_settings(model), "(use_speaker_boost and speed are not sent)")
    for j in jobs:
        shown = "omitted" if j.get("settings", ...) is None and j["kind"] == "rest" else (j.get("settings") or j.get("profile") or "Veerox defaults")
        print(f"  {j['id']}  {j['kind']:8s} {j.get('fmt', 'ulaw_8000 (streaming)'):22s} settings: {shown}")
    if not args.run:
        print("dry run: nothing generated. Re-run with --run --max-chars <N> after approval.")
        return
    if not args.max_chars or total > args.max_chars:
        raise SystemExit(f"plan needs {total} chars; pass --max-chars >= {total} (nothing sent)")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    for j in jobs:
        if j["kind"] == "rest":
            data = rest(voice_id, model, text, j["fmt"], j["settings"])
            (out / f"{j['id']}.{'ulaw' if j['fmt'].startswith('ulaw') else 'mp3'}").write_bytes(data)
        else:
            (out / f"{j['id']}.ulaw").write_bytes(asyncio.run(pipeline(text, j["profile"])))
        print(f"  wrote {j['id']}")
    print(f"done in {time.monotonic() - started:.0f}s; {total} characters submitted. Measure with: python scripts\\voice_audio_metrics.py {out}\\*")


if __name__ == "__main__":
    main()
