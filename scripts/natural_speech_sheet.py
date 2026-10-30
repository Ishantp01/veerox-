#!/usr/bin/env python
"""Natural-speech Phase 2: offline comparison sheet, character/cost estimate, local latency simulation.

Makes NO network request and generates NO audio. It reads docs/natural-speech-eval/phrases.json and
candidates.json and prints:

* ``--sheet``    a CSV with one row per candidate x phrase and blank rating columns (accent, pronunciation,
                 intonation, natural pauses, conversational flow, human-like) for a listener to fill in;
                 ``first_audio_ms`` stays "not measured" until the approved paid run fills it.
* ``--cost``     characters per experiment and the cost at a price per 1K characters YOU supply.
* ``--latency``  how long PhraseBuffer holds the first words versus the provider's own default first chunk,
                 for assumed text speeds. A model of buffering only: it cannot see the provider's synthesis time.

Usage (repo root):
    python scripts/natural_speech_sheet.py --sheet > sheet.csv
    python scripts/natural_speech_sheet.py --cost --price-per-1k 0.30
    python scripts/natural_speech_sheet.py --latency
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.api.channels.voice.elevenlabs_client import PhraseBuffer  # noqa: E402
from apps.api.channels.voice.voice_profile import VoiceProfileError, parse_profile  # noqa: E402

EVAL_DIR = ROOT / "docs" / "natural-speech-eval"
PROVIDER_FIRST_CHUNK_CHARS = 120  # documented default chunk_length_schedule[0], see voice_profile.py
RATES_CHARS_PER_SECOND = (100, 200, 400)  # assumed speed of the model's text stream (slow / typical / fast)

RATING_COLUMNS = [
    "indian_accent", "pronunciation", "rhythm", "intonation", "pauses", "naturalness", "first_audio_ms",
]

# Experiment -> (candidate-name prefixes, phrase predicate). Each phrase is synthesized once per candidate.
EXPERIMENTS = {
    "E1 script strategy (baseline config, Roman vs Devanagari, every pair)": (
        ("N0",), lambda p: True,
    ),
    "E2 phrase flush and maximum hold (N0, N1, N2, N4; Roman script, incl. the run-on stress sentence)": (
        ("N0", "N1", "N2", "N4"), lambda p: p["script"] == "roman" and p["category"] != "english_reference",
    ),
    "E3 production model (N3, Roman script, same rows as E2)": (
        ("N3",), lambda p: p["script"] == "roman" and p["category"] != "english_reference",
    ),
}


def load() -> tuple[list[dict], list[dict]]:
    phrases = json.loads((EVAL_DIR / "phrases.json").read_text(encoding="utf-8"))["phrases"]
    cands = json.loads((EVAL_DIR / "candidates.json").read_text(encoding="utf-8"))
    for c in cands:
        try:
            parse_profile(c.get("profile") or {})
        except VoiceProfileError as exc:
            raise SystemExit(f"candidate {c['name']}: {exc}")
    ids = [p["id"] for p in phrases]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate phrase ids")
    return phrases, cands


def sheet(phrases: list[dict], cands: list[dict]) -> str:
    import io

    buf = io.StringIO()
    cols = ["candidate", "phrase_id", "category", "script", "chars"] + RATING_COLUMNS + ["notes"]
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for c in cands:
        for p in phrases:
            row = {"candidate": c["name"].split(" ")[0], "phrase_id": p["id"], "category": p["category"],
                   "script": p["script"], "chars": len(p["text"]), "notes": ""}
            row.update({k: "" for k in RATING_COLUMNS})
            row["first_audio_ms"] = "not measured"
            w.writerow(row)
    return buf.getvalue()


def cost(phrases: list[dict], cands: list[dict], price: float | None, price_v2: float | None = None) -> str:
    out = []
    grand = 0
    grand_money = 0.0
    for name, (prefixes, pred) in EXPERIMENTS.items():
        chosen = [p for p in phrases if pred(p)]
        chars = sum(len(p["text"]) for p in chosen)
        n = len([c for c in cands if c["name"].split(" ")[0] in prefixes])
        total = chars * n
        grand += total
        unit = price_v2 if (prefixes == ("N3",) and price_v2 is not None) else price  # N3 is multilingual v2
        money = unit is not None
        est = f" -> {total / 1000 * unit:.4f} at {unit}/1K chars" if money else ""
        grand_money += total / 1000 * unit if money else 0.0
        out.append(f"{name}: {len(chosen)} phrases, {chars} chars per candidate x {n} = {total} chars{est}")
    est = f" -> {grand_money:.4f}" if price is not None else ""
    out.append(f"TOTAL: {grand} chars (one take each){est}")
    out.append("Each extra take of the whole set multiplies the total. Cost per char depends on your plan; supply it.")
    return "\n".join(out)


def first_send_seconds(
    text: str,
    rate: float,
    min_chars: int | None = None,
    max_hold_chars: int | None = None,
    max_hold_ms: int | None = None,
) -> tuple[float, float]:
    """(baseline, phrase-flush) time in seconds at which the first text reaches the provider.

    Words arrive at ``rate`` characters per second. Baseline = the provider's first-chunk threshold or the end of
    the text, whichever is first. Phrase flush = first PhraseBuffer release, or the end of the text; with a
    maximum hold, the buffer's own timer (its deadline) can release earlier. SIMULATED buffering only."""
    words = [w + " " for w in text.split(" ")]
    now = [0.0]
    buf = PhraseBuffer(
        min_chars, None, max_hold_chars, max_hold_ms / 1000 if max_hold_ms else None, clock=lambda: now[0]
    )
    seen = 0
    base: float | None = None
    flush: float | None = None
    for w in words:
        t = (seen + len(w)) / rate
        due = buf.deadline()
        if flush is None and due is not None and due <= t:
            now[0] = due  # the timer fires between two words
            if buf.poll():
                flush = due
        now[0] = t
        seen += len(w)
        if base is None and seen >= PROVIDER_FIRST_CHUNK_CHARS:
            base = t
        if flush is None and buf.feed(w):
            flush = t
    end = len(text) / rate
    return (base if base is not None else end), (flush if flush is not None else end)


def latency(phrases: list[dict]) -> str:
    lines = ["Added hold before the first text reaches the provider, phrase flush minus provider default (ms).",
             "Negative = phrase flush sends EARLIER. Excludes provider synthesis time (same text, but a smaller first chunk).",
             ""]
    rows = list(phrases)
    stress = {"id": "STRESS_no_punct", "text": " ".join(["bolna"] * 40)}  # 240 chars, no sentence punctuation
    for min_chars in (None, 8):
        label = "min_chars=12 (default)" if min_chars is None else f"min_chars={min_chars}"
        lines.append(f"[{label}]")
        for rate in RATES_CHARS_PER_SECOND:
            deltas = []
            for p in rows:
                b, f = first_send_seconds(p["text"], rate, min_chars)
                deltas.append((f - b) * 1000)
            sb, sf = first_send_seconds(stress["text"], rate, min_chars)
            deltas.sort()
            p95 = deltas[min(len(deltas) - 1, int(round(0.95 * (len(deltas) - 1))))]
            lines.append(f"  {rate:>3} chars/s: median {statistics.median(deltas):7.0f} ms   p95 {p95:7.0f} ms   "
                         f"max {max(deltas):7.0f} ms   ({len(deltas)} phrases)   "
                         f"unpunctuated 240-char reply: {(sf - sb) * 1000:+.0f} ms")
        lines.append("")
    lines.append("Same simulation WITH a maximum hold (phrase_max_hold_chars=100, phrase_max_hold_ms=500):")
    for rate in RATES_CHARS_PER_SECOND:
        deltas = []
        for p in rows:
            b, f = first_send_seconds(p["text"], rate, None, 100, 500)
            deltas.append((f - b) * 1000)
        deltas.sort()
        p95 = deltas[min(len(deltas) - 1, int(round(0.95 * (len(deltas) - 1))))]
        sb, sf = first_send_seconds(stress["text"], rate, None, 100, 500)
        lines.append(f"  {rate:>3} chars/s: median {statistics.median(deltas):7.0f} ms   p95 {p95:7.0f} ms   "
                     f"max {max(deltas):7.0f} ms   unpunctuated 240-char reply: {(sf - sb) * 1000:+.0f} ms")
    lines.append("")
    lines.append("SIMULATED BUFFERING ONLY, not first-audio latency. Per phrase at 200 chars/s (default min_chars): "
                 "baseline_ms / flush_ms / chars")
    for p in rows:
        b, f = first_send_seconds(p["text"], 200)
        lines.append(f"  {p['id']:<14} {b * 1000:6.0f} / {f * 1000:6.0f} / {len(p['text'])}")
    return "\n".join(lines)


def jobs(phrases: list[dict], cands: list[dict], seed: int, takes: int) -> list[dict]:
    """The exact synthesis jobs of every experiment, in a seeded random order with blind ids, so a rater cannot
    tell the candidate from the file name. Character totals are the same ones ``--cost`` reports."""
    import hashlib
    import random

    out = []
    for exp, (prefixes, pred) in EXPERIMENTS.items():
        for c in cands:
            key = c["name"].split(" ")[0]
            if key not in prefixes:
                continue
            for p in phrases:
                if pred(p):
                    for take in range(1, takes + 1):
                        out.append({"experiment": exp.split(" ")[0], "candidate": key, "phrase_id": p["id"],
                                    "take": take, "chars": len(p["text"]), "profile": c["profile"]})
    random.Random(seed).shuffle(out)
    for i, j in enumerate(out, 1):
        j["order"] = i
        j["blind_id"] = hashlib.sha1(f"{seed}|{j['experiment']}|{j['candidate']}|{j['phrase_id']}|{j['take']}".encode()).hexdigest()[:8]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--cost", action="store_true")
    ap.add_argument("--latency", action="store_true")
    ap.add_argument("--jobs", action="store_true", help="print the exact job list (JSON), shuffled with blind ids")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--takes", type=int, default=1)
    ap.add_argument("--price-per-1k", type=float, help="price per 1K chars for flash/turbo (N0-N2)")
    ap.add_argument("--price-v2-per-1k", type=float, help="price per 1K chars for multilingual v2 (N3)")
    args = ap.parse_args()
    phrases, cands = load()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if args.sheet:
        sys.stdout.write(sheet(phrases, cands))
    if args.cost:
        print(cost(phrases, cands, args.price_per_1k, args.price_v2_per_1k))
    if args.latency:
        print(latency(phrases))
    if args.jobs:
        print(json.dumps(jobs(phrases, cands, args.seed, args.takes), ensure_ascii=False, indent=1))
    if not (args.sheet or args.cost or args.latency or args.jobs):
        ap.print_help()


if __name__ == "__main__":
    main()
