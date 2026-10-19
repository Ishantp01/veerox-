#!/usr/bin/env python
"""Voice model comparison harness (Phase 2).

Builds a results table for a set of test phrases x candidate voice configs so
a human can compare them. It never invents results:

* Hindi pronunciation / Hinglish naturalness / expressiveness are MANUAL
  rating columns, left blank.
* First-audio latency is measured only with ``--measure`` AND a real
  ``ELEVENLABS_API_KEY`` in the environment, and only for ElevenLabs
  candidates (it is the time from sending the phrase to the first audio
  chunk of one fresh stream-input socket; it excludes the OpenAI model time
  and the network path to the phone). Everything else prints "not measured".
  The OpenAI-native voice path needs a live Realtime session, so it is
  always "not measured" here.
* Interruption compatibility is a static fact read from the code (see
  ``INTERRUPTION_NOTES``), not a test result.
* Cost is characters x the price per 1K characters YOU supply
  (``--price-per-1k`` / ``VOICE_COMPARE_PRICE_PER_1K_CHARS`` or per candidate
  ``--price name=value``). No prices are built in; without one only the
  character count is shown.

Usage (from the repo root):
    python scripts/voice_compare.py                       # table, nothing measured
    python scripts/voice_compare.py --categories hindi hinglish --format csv
    ELEVENLABS_API_KEY=... python scripts/voice_compare.py --measure --price-per-1k 0.30
    python scripts/voice_compare.py --candidates-file my_candidates.json

``--candidates-file`` is a JSON list of
``{"name": "...", "provider": "elevenlabs"|"openai", "profile": {...}}`` where
``profile`` is a voice profile (see apps/api/channels/voice/voice_profile.py).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.channels.voice import elevenlabs_client, voice_preview  # noqa: E402
from apps.api.channels.voice.voice_profile import (  # noqa: E402
    VoiceProfileError,
    parse_profile,
    resolve_profile,
)
from apps.api.channels.voice.tts_normalize import normalize_text  # noqa: E402
from apps.api.config import settings  # noqa: E402

NOT_MEASURED = "not measured"

INTERRUPTION_NOTES = {
    "elevenlabs": (
        "Supported: barge-in/cut closes the per-turn ElevenLabs socket "
        "(adapter._teardown_elevenlabs_turn) and sends the provider 'clear' "
        "(adapter._send_clear); OpenAI keeps running text-only and is muted locally."
    ),
    "openai": (
        "Supported: barge-in/cut sends the provider 'clear' (adapter._send_clear) and "
        "mutes the response locally (no response.cancel mid-generation, by design). "
        "Selected globally by VOICE_TTS_PROVIDER=openai; not per-org."
    ),
}

DEFAULT_CANDIDATES: list[dict[str, Any]] = [
    {"name": "elevenlabs_flash_v2_5", "provider": "elevenlabs", "profile": {"model_id": "eleven_flash_v2_5"}},
    {"name": "elevenlabs_turbo_v2_5", "provider": "elevenlabs", "profile": {"model_id": "eleven_turbo_v2_5"}},
    {"name": "openai_native", "provider": "openai", "profile": {}},
]
COLUMNS = [
    "candidate", "phrase_id", "category", "chars_sent", "hindi_pronunciation",
    "hinglish_naturalness", "expressiveness", "first_audio_ms",
    "interruption_compat", "est_cost",
]


def _load_candidates(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return DEFAULT_CANDIDATES
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for cand in data:
        if cand.get("provider") not in ("elevenlabs", "openai") or not cand.get("name"):
            raise SystemExit(f"bad candidate (need name + provider elevenlabs|openai): {cand}")
        if cand["provider"] == "elevenlabs" and cand.get("profile"):
            try:
                parse_profile(cand["profile"])
            except VoiceProfileError as exc:
                raise SystemExit(f"candidate {cand['name']}: {exc}") from exc
    return data


def _prices(args: argparse.Namespace) -> tuple[float | None, dict[str, float]]:
    base_raw = args.price_per_1k if args.price_per_1k is not None else os.environ.get("VOICE_COMPARE_PRICE_PER_1K_CHARS")
    base = float(base_raw) if base_raw not in (None, "") else None
    per: dict[str, float] = {}
    for item in args.price or []:
        name, _, value = item.partition("=")
        per[name] = float(value)
    return base, per


def _cost(chars: int, price: float | None) -> str:
    if price is None:
        return f"{chars} chars (no price given)"
    return f"{chars / 1000 * price:.4f}"


def _sent_text(text: str, cand: dict[str, Any]) -> str:
    """The text the TTS provider would receive for this candidate."""
    if cand["provider"] != "elevenlabs":
        return text
    voice = resolve_profile(cand.get("profile") or None)
    if voice is not None and voice.normalize:
        return normalize_text(text, language=voice.language, pronunciations=voice.pronunciations)
    return text


async def build_rows(args: argparse.Namespace) -> list[dict[str, str]]:
    phrases = voice_preview.load_phrases()
    if args.categories:
        phrases = [p for p in phrases if p["category"] in set(args.categories)]
    if not phrases:
        raise SystemExit("no phrases selected")
    candidates = _load_candidates(args.candidates_file)
    base_price, per_price = _prices(args)
    can_measure = args.measure and elevenlabs_client.is_configured()
    if args.measure and not can_measure:
        print("note: --measure ignored, ELEVENLABS_API_KEY is not set", file=sys.stderr)
    rows: list[dict[str, str]] = []
    for cand in candidates:
        voice = resolve_profile(cand.get("profile") or None) if cand["provider"] == "elevenlabs" else None
        price = per_price.get(cand["name"], base_price)
        for phrase in phrases:
            sent = _sent_text(phrase["text"], cand)
            latency = NOT_MEASURED
            if can_measure and cand["provider"] == "elevenlabs":
                try:
                    result = await voice_preview.synthesize(phrase["text"], voice)
                    latency = str(result.first_audio_ms)
                except Exception as exc:  # noqa: BLE001
                    latency = f"failed: {exc}"
            rows.append({
                "candidate": cand["name"],
                "phrase_id": phrase["id"],
                "category": phrase["category"],
                "chars_sent": str(len(sent)),
                "hindi_pronunciation": "",
                "hinglish_naturalness": "",
                "expressiveness": "",
                "first_audio_ms": latency,
                "interruption_compat": INTERRUPTION_NOTES[cand["provider"]],
                "est_cost": _cost(len(sent), price),
            })
    return rows


def render(rows: list[dict[str, str]], fmt: str) -> str:
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(row[c].replace("|", "/") for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--categories", nargs="*", help="only these phrase categories")
    parser.add_argument("--candidates-file", help="JSON list of candidates (see docstring)")
    parser.add_argument("--measure", action="store_true", help="measure first-audio latency (needs ELEVENLABS_API_KEY)")
    parser.add_argument("--price-per-1k", type=float, help="your price per 1K characters")
    parser.add_argument("--price", action="append", metavar="NAME=VALUE", help="per-candidate price per 1K chars")
    parser.add_argument("--format", choices=("md", "csv"), default="md")
    parser.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args()
    text = render(asyncio.run(build_rows(args)), args.format)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stdout.write(text)
    if settings.voice_tts_provider:
        print(f"(platform VOICE_TTS_PROVIDER={settings.voice_tts_provider}; model={settings.elevenlabs_model_id})", file=sys.stderr)


if __name__ == "__main__":
    main()
