"""Make short Hinglish phone-style clips with Sarvam (and ElevenLabs, if a key is set) to listen to side by side.

    set SARVAM_API_KEY=...            (PowerShell: $env:SARVAM_API_KEY="...")
    python scripts/voice_audition.py --speakers aparna_hi_customer,anushka --out audition

Writes <out>/sarvam_<speaker>_<n>.wav and <out>/elevenlabs_<n>.mp3. Nothing here touches the app or the database.

UNVERIFIED: the Sarvam request/response shape below is from memory of their REST docs (POST /text-to-speech,
``api-subscription-key`` header, base64 WAV in ``audios``). If it errors, the printed response says which field is wrong.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

LINES = [
    "Namaste, main Acme ka AI assistant Veer bol raha hoon. Main aapki kya madad kar sakta hoon?",
    "Ji bilkul, samajh gaya... aap bas itna bataiye, aapka budget kitna rakha hai?",
    "Haan haan, koi problem nahi. Main aapko WhatsApp par details bhej deta hoon, aap aaram se dekh lijiye.",
    "The franchise investment starts at around five lakh rupees, aur usme setup bhi included hai.",
]


def _post(url: str, headers: dict[str, str], body: dict) -> bytes:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{url} -> HTTP {exc.code}: {exc.read().decode(errors='replace')[:400]}")


def sarvam(out: Path, speakers: list[str], model: str, lang: str, pace: float) -> None:
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        print("SARVAM_API_KEY not set - skipping Sarvam")
        return
    for speaker in speakers:
        for n, text in enumerate(LINES, 1):
            raw = _post(
                "https://api.sarvam.ai/text-to-speech",
                {"api-subscription-key": key},
                {"text": text, "target_language_code": lang, "speaker": speaker, "model": model,
                 "pace": pace, "speech_sample_rate": 24000},
            )
            audios = json.loads(raw).get("audios") or []
            if not audios:
                raise SystemExit(f"no audio in Sarvam response: {raw[:300]!r}")
            path = out / f"sarvam_{speaker}_{n}.wav"
            path.write_bytes(base64.b64decode(audios[0]))
            print("wrote", path)


def elevenlabs(out: Path) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    voice = os.environ.get("ELEVENLABS_VOICE_ID")
    if not (key and voice):
        print("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not set - skipping ElevenLabs")
        return
    model = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    for n, text in enumerate(LINES, 1):
        audio = _post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            {"xi-api-key": key},
            {"text": text, "model_id": model,
             "voice_settings": {"stability": 0.45, "similarity_boost": 0.80}},
        )
        path = out / f"elevenlabs_{n}.mp3"
        path.write_bytes(audio)
        print("wrote", path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speakers", default="aparna_hi_customer", help="comma-separated Sarvam speaker names")
    ap.add_argument("--model", default="bulbul:v4-flash")
    ap.add_argument("--lang", default="hi-IN")
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--out", default="audition")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sarvam(out, [s.strip() for s in args.speakers.split(",") if s.strip()], args.model, args.lang, args.pace)
    elevenlabs(out)


if __name__ == "__main__":
    main()
