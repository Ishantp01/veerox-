#!/usr/bin/env python
"""Voice A/B sample generator with a blind rating page.

Synthesises the same phrases through the app's own ElevenLabs streaming session (the code path a call uses)
for several candidate configurations, saves labelled WAVs, and builds a BLIND page for rating them.

* Talks only to api.elevenlabs.io. No telephony, no database, no production code path is touched.
* Dry run by default: prints the exact character plan and the credit estimate and spends nothing.
  ``--run`` is required to synthesise, and ``--max-chars`` (default 5200) is a hard cap checked before
  every request, so the plan can never silently exceed what was approved.
* Text is fed as small word-sized deltas (like LLM tokens) so ``VOICE_TTS_PHRASE_FLUSH`` is exercised
  exactly as in a call.
* Output: <out>/labeled/<candidate>__<phrase>.wav, <out>/blind/sNN.wav, <out>/key.json (the answer key),
  <out>/rate.html (blind page, exports ratings JSON), <out>/timings.csv.
* ``--score ratings.json`` un-blinds ratings and prints the mean per candidate.

    python scripts/voice_ab_samples.py                  # plan + estimate only
    python scripts/voice_ab_samples.py --run            # synthesise (paid)
    python scripts/voice_ab_samples.py --score <out>/ratings.json
"""

# ruff: noqa: E501  (long Hinglish phrases and the HTML template are string literals)
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import html
import json
import random
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.api.channels.voice import elevenlabs_client, voice_preview  # noqa: E402
from apps.api.channels.voice.voice_profile import platform_voice_id, resolve_profile  # noqa: E402
from apps.api.config import settings  # noqa: E402

LONG_PHRASE = {
    "id": "long_hinglish",
    "text": (
        "Dekhiye, aapke business mein roz kitne customer calls aate hain, uska hisaab rakhna mushkil hota hai. "
        "Hamara assistant har call ka record bana deta hai, aur zaroorat padne par WhatsApp par details bhi "
        "bhej deta hai. Kya main aapko ek chhota sa demo dikha doon?"
    ),
}
PHRASE_IDS = [
    "pilot_greeting_roman",
    "pilot_greeting_devanagari",
    "pilot_purpose_roman",
    "hinglish_mixed",
    "en_confirm",
]
FLASH = "eleven_flash_v2_5"
TURBO = "eleven_turbo_v2_5"
MULTI = "eleven_multilingual_v2"
# name -> (profile, phrase_flush, phrase subset: "all" | "no_long")
CANDIDATES: dict[str, tuple[dict[str, Any], bool, str]] = {
    "C1_flash_baseline": ({"model_id": FLASH}, False, "all"),
    "C2_turbo": ({"model_id": TURBO}, False, "all"),
    "C3_multilingual_v2": ({"model_id": MULTI}, False, "all"),
    "C4_flash_phrase_flush": ({"model_id": FLASH}, True, "all"),
    "C5_multilingual_phrase_flush": ({"model_id": MULTI}, True, "all"),
    "C6_flash_stability_035": (
        {"model_id": FLASH, "stability": 0.35, "similarity_boost": 0.75},
        False,
        "no_long",
    ),
}
# Raw (24 kHz PCM, no phone band) vs phone (8 kHz mu-law) of the same text and model: isolates telephone degradation.
RAW_PHRASES = ["pilot_purpose_roman", "pilot_greeting_roman"]
RAW_MODEL = FLASH
DELTA_RE = re.compile(r"\s*\S+\s?")


def _phrases() -> dict[str, str]:
    table = {p["id"]: p["text"] for p in voice_preview.load_phrases()}
    out = {pid: table[pid] for pid in PHRASE_IDS}
    out[LONG_PHRASE["id"]] = LONG_PHRASE["text"]
    return out


def plan(phrases: dict[str, str]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for name, (profile, flush, subset) in CANDIDATES.items():
        for pid, text in phrases.items():
            if subset == "no_long" and pid == LONG_PHRASE["id"]:
                continue
            jobs.append(
                {
                    "candidate": name,
                    "phrase": pid,
                    "text": text,
                    "profile": profile,
                    "flush": flush,
                    "kind": "phone",
                    "model": profile["model_id"],
                }
            )
    for pid in RAW_PHRASES:
        jobs.append(
            {
                "candidate": "R1_flash_raw_24k",
                "phrase": pid,
                "text": phrases[pid],
                "profile": {"model_id": RAW_MODEL},
                "flush": False,
                "kind": "raw",
                "model": RAW_MODEL,
            }
        )
    return jobs


def estimate(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    chars: dict[str, int] = {}
    for j in jobs:
        chars[j["model"]] = chars.get(j["model"], 0) + len(j["text"])
    total = sum(chars.values())
    # Official rule: Multilingual v2 = 1 credit/char; Flash/Turbo = 0.5-1 credit/char (0.5 is the usual API rate).
    low = sum(n * (1.0 if m == MULTI else 0.5) for m, n in chars.items())
    high = float(total)
    return {
        "jobs": len(jobs),
        "chars_by_model": chars,
        "total_chars": total,
        "credits_low": low,
        "credits_high": high,
    }


def ulaw_to_pcm16(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        u = ~b & 0xFF
        sample = (((u & 0x0F) << 3) + 0x84) << ((u >> 4) & 7)
        sample -= 0x84
        out += struct.pack("<h", -sample if u & 0x80 else sample)
    return bytes(out)


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


async def synth_phone(job: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Through the app's own streaming session, fed with word-sized deltas."""
    settings.voice_tts_phrase_flush = job["flush"]
    voice = resolve_profile(job["profile"])
    first_ms: int | None = None
    chunks: list[bytes] = []
    async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
        started = time.monotonic()

        async def collect() -> None:
            nonlocal first_ms
            async for b64 in session.audio_chunks():
                if first_ms is None:
                    first_ms = round((time.monotonic() - started) * 1000)
                chunks.append(base64.b64decode(b64))

        reader = asyncio.create_task(collect())
        for piece in DELTA_RE.findall(job["text"]):
            await session.send_text(piece)
            await asyncio.sleep(0.025)  # roughly how fast an LLM streams tokens
        await session.finish()
        await asyncio.wait_for(reader, timeout=30)
        sent = session.chars_sent
        failed = session.failed
    return ulaw_to_pcm16(b"".join(chunks)), {
        "first_audio_ms": first_ms,
        "chars_sent": sent,
        "failed": failed,
    }


def _synth_raw_sync(job: dict[str, Any]) -> bytes:
    voice = resolve_profile(job["profile"])
    body = {
        "text": job["text"],
        "model_id": job["model"],
        "voice_settings": voice.voice_settings if voice else {},
    }
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{platform_voice_id()}?output_format=pcm_24000"
    )
    headers = {"Content-Type": "application/json", "xi-api-key": settings.elevenlabs_api_key or ""}
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"raw TTS HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}"
        ) from exc


async def run(jobs: list[dict[str, Any]], out: Path, max_chars: int, seed: int) -> None:
    if not elevenlabs_client.is_configured():
        raise SystemExit("ELEVENLABS_API_KEY is not set")
    est = estimate(jobs)
    if est["total_chars"] > max_chars:
        raise SystemExit(
            f"plan is {est['total_chars']} chars, over --max-chars {max_chars}; nothing sent"
        )
    (out / "labeled").mkdir(parents=True, exist_ok=True)
    (out / "blind").mkdir(parents=True, exist_ok=True)
    spent = 0
    rows: list[dict[str, Any]] = []
    for n, job in enumerate(jobs, 1):
        if spent + len(job["text"]) > max_chars:
            raise SystemExit(f"stopping before job {n}: would exceed --max-chars {max_chars}")
        spent += len(job["text"])
        meta: dict[str, Any] = {
            "first_audio_ms": None,
            "chars_sent": len(job["text"]),
            "failed": False,
        }
        if job["kind"] == "raw":
            pcm = await asyncio.get_running_loop().run_in_executor(None, _synth_raw_sync, job)
            rate = 24000
        else:
            pcm, meta = await synth_phone(job)
            rate = 8000
        if not pcm or meta["failed"]:
            print(
                f"[{n}/{len(jobs)}] {job['candidate']} {job['phrase']}: FAILED or empty - not retried"
            )
            continue
        label = f"{job['candidate']}__{job['phrase']}.wav"
        write_wav(out / "labeled" / label, pcm, rate)
        rows.append(
            {
                **{k: job[k] for k in ("candidate", "phrase", "kind", "flush", "model")},
                "file": label,
                "first_audio_ms": meta["first_audio_ms"],
                "chars": len(job["text"]),
                "seconds": round(len(pcm) / 2 / rate, 2),
            }
        )
        print(
            f"[{n}/{len(jobs)}] {job['candidate']:30s} {job['phrase']:26s} first_audio_ms={meta['first_audio_ms']}"
        )
    print(f"characters submitted: {spent} (cap {max_chars})")
    build_blind(rows, out, seed)


def build_blind(rows: list[dict[str, Any]], out: Path, seed: int) -> None:
    rng = random.Random(seed)
    by_phrase: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_phrase.setdefault(r["phrase"], []).append(r)
    key: dict[str, dict[str, Any]] = {}
    sections = []
    counter = 0
    texts = _phrases()
    for pid, items in by_phrase.items():
        rng.shuffle(items)
        cards = []
        for r in items:
            counter += 1
            sid = f"s{counter:02d}"
            (out / "blind" / f"{sid}.wav").write_bytes((out / "labeled" / r["file"]).read_bytes())
            key[sid] = {
                "candidate": r["candidate"],
                "phrase": pid,
                "model": r["model"],
                "flush": r["flush"],
            }
            selects = "".join(
                f'<label>{lbl} <select data-k="{k}"><option value="">-</option>'
                + "".join(f"<option>{i}</option>" for i in range(1, 6))
                + "</select></label>"
                for k, lbl in (
                    ("naturalness", "Natural"),
                    ("pronunciation", "Pronunciation"),
                    ("expressiveness", "Expressive"),
                )
            )
            cards.append(
                f'<div class="card" data-id="{sid}"><b>Clip {sid}</b>'
                f'<audio controls preload="none" src="blind/{sid}.wav"></audio>{selects}'
                '<input data-k="notes" placeholder="notes"></div>'
            )
        sections.append(
            f"<h2>{html.escape(pid)}</h2><p class='t'>{html.escape(texts.get(pid, ''))}</p>"
            + "".join(cards)
        )
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    with open(out / "timings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["none"])
        w.writeheader()
        w.writerows(rows)
    (out / "rate.html").write_text(PAGE.replace("__BODY__", "\n".join(sections)), encoding="utf-8")
    print(
        f"blind page: {out / 'rate.html'} (answer key: {out / 'key.json'} - do not open before rating)"
    )


PAGE = """<!doctype html><meta charset=utf-8><title>Blind voice rating</title>
<style>body{font:15px system-ui;max-width:820px;margin:24px auto;padding:0 16px}
.card{border:1px solid #ccc;border-radius:8px;padding:10px;margin:8px 0;display:flex;flex-wrap:wrap;gap:10px;
align-items:center}.t{color:#555}audio{width:260px}select,input{font:inherit}input{flex:1;min-width:140px}
button{font:inherit;padding:8px 16px}</style>
<h1>Blind voice rating</h1><p>Rate each clip 1 (poor) to 5 (excellent). Clip order is shuffled and names are
hidden. Clips under one sentence are comparable with each other. Use headphones at the same volume.</p>
__BODY__
<p><button onclick="exp()">Export ratings (downloads ratings.json)</button></p>
<script>function exp(){const r={};document.querySelectorAll('.card').forEach(c=>{const o={};
c.querySelectorAll('[data-k]').forEach(e=>o[e.dataset.k]=e.value);r[c.dataset.id]=o});
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(r,null,1)],
{type:'application/json'}));a.download='ratings.json';a.click()}</script>"""


def score(ratings_path: Path) -> None:
    ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
    key = json.loads((ratings_path.parent / "key.json").read_text(encoding="utf-8"))
    agg: dict[str, dict[str, list[int]]] = {}
    for sid, r in ratings.items():
        cand = key[sid]["candidate"]
        for k in ("naturalness", "pronunciation", "expressiveness"):
            if str(r.get(k, "")).isdigit():
                agg.setdefault(cand, {}).setdefault(k, []).append(int(r[k]))
    print(f"{'candidate':32s}{'natural':>9s}{'pronun':>9s}{'express':>9s}{'n':>4s}")
    for cand, vals in sorted(agg.items()):
        m = {k: sum(v) / len(v) for k, v in vals.items()}
        print(
            f"{cand:32s}{m.get('naturalness', 0):9.2f}{m.get('pronunciation', 0):9.2f}"
            f"{m.get('expressiveness', 0):9.2f}{len(vals.get('naturalness', [])):4d}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--run", action="store_true", help="synthesise (PAID); without it only the plan is printed"
    )
    ap.add_argument("--max-chars", type=int, default=5200)
    ap.add_argument("--out", default=".validation-voice-ab")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--score", help="ratings.json exported from rate.html")
    args = ap.parse_args()
    if args.score:
        score(Path(args.score))
        return
    jobs = plan(_phrases())
    est = estimate(jobs)
    print(f"jobs: {est['jobs']}  total characters: {est['total_chars']}  (cap {args.max_chars})")
    for m, n in est["chars_by_model"].items():
        print(f"  {m}: {n} chars")
    print(
        f"estimated credits: {est['credits_low']:.0f} - {est['credits_high']:.0f} "
        "(Multilingual v2 = 1/char; Flash/Turbo 0.5-1/char)"
    )
    for j in jobs:
        print(
            f"  {j['candidate']:30s} {j['phrase']:26s} {len(j['text']):4d} chars  flush={j['flush']}  {j['kind']}"
        )
    if not args.run:
        print("dry run - nothing was sent. Re-run with --run to synthesise.")
        return
    asyncio.run(run(jobs, Path(args.out), args.max_chars, args.seed))


if __name__ == "__main__":
    main()
