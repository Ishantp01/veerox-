#!/usr/bin/env python
"""Synthesize preview WAVs for a voice profile from the command line.

Needs ELEVENLABS_API_KEY in the environment (exits with a clear message
otherwise). Writes one <phrase_id>.wav per phrase into --out-dir.

    python scripts/voice_preview.py --out-dir out --profile '{"model_id":"eleven_turbo_v2_5","normalize_speech":true}'
    python scripts/voice_preview.py --out-dir out --text "Namaste, aapka bill Rs. 1500 hai."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.channels.voice import voice_preview  # noqa: E402
from apps.api.channels.voice.voice_profile import VoiceProfileError, parse_profile, resolve_profile  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    voice = None
    if args.profile:
        try:
            parse_profile(json.loads(args.profile))
        except (VoiceProfileError, json.JSONDecodeError) as exc:
            print(f"invalid profile: {exc}", file=sys.stderr)
            return 2
        voice = resolve_profile(json.loads(args.profile))
    items = [("custom", args.text)] if args.text else [
        (p["id"], p["text"]) for p in voice_preview.load_phrases() if not args.ids or p["id"] in args.ids
    ]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for pid, text in items:
        try:
            result = await voice_preview.synthesize(text, voice)
        except voice_preview.PreviewUnavailable as exc:
            print(f"cannot preview: {exc}", file=sys.stderr)
            return 3
        except voice_preview.PreviewFailed as exc:
            print(f"{pid}: failed: {exc}", file=sys.stderr)
            continue
        (out / f"{pid}.wav").write_bytes(voice_preview.ulaw_wav(result.audio))
        print(f"{pid}: wrote {pid}.wav (first audio {result.first_audio_ms} ms)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--profile", help="voice profile as JSON (default: platform voice)")
    parser.add_argument("--text", help="one custom phrase instead of the phrase set")
    parser.add_argument("--ids", nargs="*", help="only these phrase ids")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
