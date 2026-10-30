#!/usr/bin/env python
"""Blind audio runner for the natural-speech listening experiment (78 jobs; docs/natural-speech-phase3.md).

DEFAULT = OFFLINE. Without both ``--run`` and ``--allow-provider-requests`` this script makes ZERO provider
requests and generates no audio. It only writes, under ``--out``:

    listening/sheet.csv      the rating sheet: anonymous clip ids and candidate labels, the text, blank rating columns
    listening/README.txt     how to rate
    KEY/key.json             clip id -> experiment, candidate, label, exact profile and the effective voice settings
    KEY/results.jsonl        one line per job (status "planned" offline; "ok"/"failed" after a paid run)

Keep ``KEY/`` away from the raters; the sheet contains nothing that identifies a configuration.

Paid run (ElevenLabs characters are billed; do not run without approval):

    python scripts/natural_speech_run.py --out runs/r1 --seed 7 --takes 1 \\
        --run --allow-provider-requests --max-chars 6900

* ``--max-chars`` is required for a paid run and is checked BEFORE the first request against the planned
  characters plus 2 per job (a trailing space and the connection handshake).
* Audio is saved as listening/clips/<clip_id>.wav (8 kHz PCM, the band of a phone call).
* A failed job is recorded as "failed" with a short error code, never dropped; the exit status is 1 if any job is
  not "ok". ``--resume`` skips jobs already "ok" and retries the rest.
* Nothing here changes any organisation, environment or platform setting.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import importlib.util
import json
import random
import re
import struct
import sys
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DELTA_RE = re.compile(r"\s*\S+\s?")  # word-sized deltas, like a streamed model reply
DELTA_SECONDS = 0.025  # about 200 characters/second, the speed the latency model assumes
RATING_COLUMNS = ["indian_accent", "pronunciation", "rhythm", "intonation", "pauses", "naturalness"]
EXPECTED_BASELINE_MODEL = "eleven_turbo_v2_5"  # the pilot model; N0 must resolve to it
SHEET_COLUMNS = ["clip_id", "set_id", "label", "category", "script", "text"] + RATING_COLUMNS + ["notes"]
OVERHEAD_PER_JOB = 2  # trailing space + handshake space: billed but not in the phrase text
README = """Listening sheet: how to rate

* Listen to each clip (listening/clips/<clip_id>.wav) without looking at anything else. Do not open the KEY folder.
* Clips with the same set_id are the same sentence in different configurations, in random order. Compare them.
* Score every column from 1 to 5:  1 = clearly unnatural / wrong,  3 = acceptable,  5 = indistinguishable from a
  natural human speaker.
    indian_accent  how natural and Indian the accent sounds (Hindi, Hinglish, and English words inside Hindi)
    pronunciation  correct sounds, vowel length, English words inside Hindi
    rhythm         pace and stress, not metronomic, not rushed
    intonation     pitch movement; questions rise, statements settle, no announcement tone
    pauses         pauses are where a person would pause, none awkward or missing
    naturalness    overall: would you believe this is a person?
* Put anything you noticed in notes (clipping, glitch, wrong word).
* Rate from the 8 kHz phone-band audio; do not judge the recording quality.
* Latency is NOT rated by ear: it is measured by the runner and analysed separately.
"""


class JobFailed(RuntimeError):
    """One job could not produce audio. Carries a short code only (never text, keys or URLs)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _load_sheet_module() -> Any:
    path = Path(__file__).with_name("natural_speech_sheet.py")
    spec = importlib.util.spec_from_file_location("natural_speech_sheet", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _labels(candidates: list[str], seed: int) -> dict[str, str]:
    """Anonymous, seed-dependent candidate labels such as 'K4' (distinct, not in candidate order)."""
    rng = random.Random(f"labels|{seed}")
    pool = [f"{a}{d}" for a in "ABCDEFGHJKLMNPQRSTUVWXYZ" for d in "23456789"]
    rng.shuffle(pool)
    return {c: pool[i] for i, c in enumerate(sorted(candidates))}


def plan(seed: int, takes: int) -> dict[str, Any]:
    """Everything the run needs, with no provider access."""
    from apps.api.channels.voice import elevenlabs_client  # noqa: F401  (imported lazily; no connection is made)
    from apps.api.channels.voice.voice_profile import (
        default_voice_settings,
        platform_model_id,
        platform_voice_id,
        resolve_profile,
    )

    sheet = _load_sheet_module()
    phrases, cands = sheet.load()
    by_id = {p["id"]: p for p in phrases}
    name_of = {c["name"].split(" ")[0]: c["name"] for c in cands}
    labels = _labels(list(name_of), seed)
    jobs = sheet.jobs(phrases, cands, seed, takes)
    for j in jobs:
        phrase = by_id[j["phrase_id"]]
        voice = resolve_profile(j["profile"] or None)
        model = voice.model_id if voice else platform_model_id()
        j.update(
            clip_id=j["blind_id"],
            set_id="S" + sheet_set_id(j["experiment"], j["phrase_id"], seed),
            label=labels[j["candidate"]],
            candidate_name=name_of[j["candidate"]],
            text=phrase["text"],
            category=phrase["category"],
            script=phrase["script"],
            effective={
                "voice_id": voice.voice_id if voice else platform_voice_id(),
                "model_id": model,
                "voice_settings": dict(voice.voice_settings) if voice else default_voice_settings(model),
                "phrase_flush": voice.phrase_flush if voice else None,
            },
        )
    return {
        "seed": seed,
        "takes": takes,
        "labels": labels,
        "jobs": jobs,
        "planned_chars": sum(j["chars"] for j in jobs),
        "estimated_billed_chars": sum(j["chars"] + OVERHEAD_PER_JOB for j in jobs),
    }


def sheet_set_id(experiment: str, phrase_id: str, seed: int) -> str:
    import hashlib

    return hashlib.sha1(f"set|{seed}|{experiment}|{phrase_id}".encode()).hexdigest()[:6]


def write_offline_files(p: dict[str, Any], out: Path, key_dir: Path) -> None:
    listening = out / "listening"
    listening.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, exist_ok=True)
    (listening / "README.txt").write_text(README, encoding="utf-8")
    with (listening / "sheet.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for j in sorted(p["jobs"], key=lambda x: (x["set_id"], x["order"])):  # same sentence together, shuffled within
            row = {c: j.get(c, "") for c in ("clip_id", "set_id", "label", "category", "script", "text")}
            row.update({c: "" for c in RATING_COLUMNS + ["notes"]})
            w.writerow(row)
    key = {k: p[k] for k in ("seed", "takes", "labels", "planned_chars", "estimated_billed_chars")}
    key["jobs"] = [
        {k: j[k] for k in ("clip_id", "set_id", "label", "experiment", "candidate", "candidate_name", "phrase_id",
                           "take", "chars", "profile", "effective")}
        for j in p["jobs"]
    ]
    (key_dir / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    ledger = key_dir / "results.jsonl"
    if not ledger.exists():
        with ledger.open("w", encoding="utf-8") as fh:
            for j in p["jobs"]:
                fh.write(json.dumps(_row(j, "planned"), ensure_ascii=False) + "\n")


def _row(job: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    row = {"clip_id": job["clip_id"], "status": status, "chars_planned": job["chars"], "chars_sent": None,
           "first_audio_ms": None, "audio_bytes": None, "error_code": None,
           "at": datetime.now(UTC).isoformat(timespec="seconds")}
    row.update(extra)
    return row


def read_ledger(key_dir: Path) -> dict[str, dict[str, Any]]:
    path = key_dir / "results.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["clip_id"]] = r  # the last line for a clip wins
    return rows


def ulaw_to_pcm16(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        u = ~b & 0xFF
        t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
        out += struct.pack("<h", (0x84 - t) if (u & 0x80) else (t - 0x84))
    return bytes(out)


def write_wav(path: Path, ulaw: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(ulaw_to_pcm16(ulaw))


async def synth_job(job: dict[str, Any], delta_seconds: float, timeout: float) -> tuple[bytes, dict[str, Any]]:
    """One job through the app's own streaming session, fed with word-sized deltas. Raises JobFailed(code)."""
    from apps.api.channels.voice import elevenlabs_client
    from apps.api.channels.voice.voice_profile import resolve_profile

    voice = resolve_profile(job["profile"] or None)
    first_ms: int | None = None
    chunks: list[bytes] = []
    try:
        async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
            started = time.monotonic()

            async def collect() -> None:
                nonlocal first_ms
                async for b64 in session.audio_chunks():
                    if first_ms is None:
                        first_ms = round((time.monotonic() - started) * 1000)
                    chunks.append(base64.b64decode(b64))

            reader = asyncio.create_task(collect())
            try:
                for piece in DELTA_RE.findall(job["text"]):
                    await session.send_text(piece)
                    if delta_seconds:
                        await asyncio.sleep(delta_seconds)
                await session.finish()
                await asyncio.wait_for(reader, timeout=timeout)
            finally:
                if not reader.done():
                    reader.cancel()
            failed, code, sent = session.failed, session.fail_code, session.chars_sent
    except asyncio.TimeoutError as exc:
        raise JobFailed("timeout") from exc
    except JobFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded by class name only
        raise JobFailed(f"error_{type(exc).__name__}") from exc
    audio = b"".join(chunks)
    if failed:
        raise JobFailed(code or "provider_error")
    if not audio:
        raise JobFailed("no_audio")
    return audio, {"chars_sent": sent, "first_audio_ms": first_ms, "audio_bytes": len(audio)}


async def run_paid(p: dict[str, Any], out: Path, key_dir: Path, *, resume: bool, delta_seconds: float, timeout: float) -> int:
    clips = out / "listening" / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    ledger = key_dir / "results.jsonl"
    done = read_ledger(key_dir)
    bad = 0
    for job in sorted(p["jobs"], key=lambda x: x["order"]):
        cid = job["clip_id"]
        if resume and done.get(cid, {}).get("status") == "ok":
            continue
        try:
            audio, stats = await synth_job(job, delta_seconds, timeout)
            write_wav(clips / f"{cid}.wav", audio)
            row = _row(job, "ok", **stats)
        except JobFailed as exc:
            row = _row(job, "failed", error_code=exc.code)
            bad += 1
        with ledger.open("a", encoding="utf-8") as fh:  # appended and flushed per job: a crash loses nothing
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return bad


def summarize(key_dir: Path, planned: int) -> str:
    rows = read_ledger(key_dir)
    counts: dict[str, int] = {}
    for r in rows.values():
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    sent = sum(r["chars_sent"] or 0 for r in rows.values())
    failed = [f"{c}:{r['error_code']}" for c, r in rows.items() if r["status"] == "failed"]
    return (f"jobs {planned}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) +
            f"; characters sent {sent}" + (f"; FAILED {failed}" if failed else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder (created)")
    ap.add_argument("--key-dir", help="where the label-to-configuration key goes (default: <out>/KEY)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--run", action="store_true", help="PAID: synthesise (also needs --allow-provider-requests)")
    ap.add_argument("--allow-provider-requests", action="store_true", help="PAID: explicit opt-in to ElevenLabs requests")
    ap.add_argument("--max-chars", type=int, help="PAID: hard cap on estimated billed characters (required with --run)")
    ap.add_argument("--baseline-model", default=EXPECTED_BASELINE_MODEL,
                    help="the model N0 must resolve to (default: the pilot's eleven_turbo_v2_5)")
    ap.add_argument("--resume", action="store_true", help="skip jobs already ok in KEY/results.jsonl")
    ap.add_argument("--delta-ms", type=float, default=DELTA_SECONDS * 1000, help="pause between streamed words")
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for one job's audio")
    args = ap.parse_args(argv)

    out = Path(args.out)
    key_dir = Path(args.key_dir) if args.key_dir else out / "KEY"
    p = plan(args.seed, args.takes)
    baseline_model = next(j["effective"]["model_id"] for j in p["jobs"] if j["candidate"] == "N0")
    write_offline_files(p, out, key_dir)
    print(f"planned {len(p['jobs'])} jobs, {p['planned_chars']} characters "
          f"(about {p['estimated_billed_chars']} billed); sheet: {out / 'listening' / 'sheet.csv'}; key: {key_dir}")

    if baseline_model != args.baseline_model:
        print(f"WARNING: the baseline N0 resolves to {baseline_model}, not {args.baseline_model}. "
              "Run with VEEROX_ENV_FILE=.env.pilot so the baseline is the pilot configuration.", file=sys.stderr)
    if not (args.run or args.allow_provider_requests):
        print("OFFLINE dry run: no provider request was made and no audio was generated.")
        return 0
    if not (args.run and args.allow_provider_requests):
        print("refusing: a paid run needs BOTH --run and --allow-provider-requests. Nothing was sent.", file=sys.stderr)
        return 2
    if args.max_chars is None:
        print("refusing: --max-chars is required for a paid run. Nothing was sent.", file=sys.stderr)
        return 2
    if p["estimated_billed_chars"] > args.max_chars:
        print(f"refusing: estimated {p['estimated_billed_chars']} billed characters exceed --max-chars "
              f"{args.max_chars}. Nothing was sent.", file=sys.stderr)
        return 2

    from apps.api.channels.voice import elevenlabs_client
    from apps.api.config import settings

    if baseline_model != args.baseline_model:
        print(f"refusing: the baseline resolves to {baseline_model}, not the pilot's {args.baseline_model} "
              "(use VEEROX_ENV_FILE=.env.pilot). Nothing was sent.", file=sys.stderr)
        return 2
    if not elevenlabs_client.is_configured():
        print("refusing: ELEVENLABS_API_KEY is not set. Nothing was sent.", file=sys.stderr)
        return 2
    if settings.voice_tts_phrase_flush and any(j["profile"].get("phrase_flush") is None for j in p["jobs"]):
        print("refusing: VOICE_TTS_PHRASE_FLUSH is on, so the baseline would not be the baseline. Nothing was sent.",
              file=sys.stderr)
        return 2

    bad = asyncio.run(run_paid(p, out, key_dir, resume=args.resume, delta_seconds=args.delta_ms / 1000,
                               timeout=args.timeout))
    print(summarize(key_dir, len(p["jobs"])))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
