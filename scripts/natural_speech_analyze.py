#!/usr/bin/env python
"""Offline analysis of the listening experiment: filled rating sheet + KEY + ledger -> per-candidate results.

Reads three local files and makes no network request:

    python scripts/natural_speech_analyze.py --key runs/r1/KEY/key.json --ledger runs/r1/KEY/results.jsonl \\
        --sheet runs/r1/listening/sheet_filled.csv

It reports, per candidate, the mean rating (1 to 5) of each dimension the raters filled in, and the measured
first-audio latency (median and p95) with its paired difference to the baseline N0 on the same sentence and take.
The latency targets (median +300 ms, p95 +500 ms over the baseline) are printed as a check, with the number of
pairs behind them: with a few dozen clips a p95 is only indicative.

Latency here is the runner's measurement, from the first word sent to the first audio chunk, on one connection
per clip, so it includes the pacing of the streamed words and the network, and varies run to run. It is a
measurement of the paid run only; nothing is estimated.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any

DIMENSIONS = ["indian_accent", "pronunciation", "rhythm", "intonation", "pauses", "naturalness"]
TARGET_MEDIAN_MS = 300
TARGET_P95_MS = 500


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def _rating(raw: str) -> float | None:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 1 <= v <= 5 else None


def analyze(key: dict[str, Any], ledger: list[dict[str, Any]], sheet: list[dict[str, str]], baseline: str = "N0") -> dict[str, Any]:
    jobs = {j["clip_id"]: j for j in key["jobs"]}
    last: dict[str, dict[str, Any]] = {}
    for row in ledger:  # the last line for a clip wins
        last[row["clip_id"]] = row
    candidates = sorted({j["candidate"] for j in jobs.values()})

    ratings: dict[str, dict[str, list[float]]] = {c: {d: [] for d in DIMENSIONS} for c in candidates}
    ignored = 0
    unknown = 0
    for r in sheet:
        job = jobs.get(r.get("clip_id", ""))
        if job is None:
            unknown += 1
            continue
        for d in DIMENSIONS:
            v = _rating(r.get(d, ""))
            if v is None:
                if (r.get(d) or "").strip():
                    ignored += 1  # something was typed but is not a number from 1 to 5
                continue
            ratings[job["candidate"]][d].append(v)

    # latency of the clips that were produced, keyed for pairing with the baseline
    lat: dict[str, dict[tuple[str, int], float]] = {c: {} for c in candidates}
    status: dict[str, dict[str, int]] = {c: {} for c in candidates}
    for cid, job in jobs.items():
        row = last.get(cid)
        st = row["status"] if row else "missing"
        status[job["candidate"]][st] = status[job["candidate"]].get(st, 0) + 1
        if row and row["status"] == "ok" and row.get("first_audio_ms") is not None:
            slot = (job["phrase_id"], job["take"])
            # the baseline's E2 clip is preferred over its E1 clip for the same sentence
            if job["candidate"] == baseline and slot in lat[baseline] and job["experiment"] != "E2":
                continue
            lat[job["candidate"]][slot] = float(row["first_audio_ms"])

    out: dict[str, Any] = {"candidates": {}, "ignored_ratings": ignored, "unknown_clips": unknown}
    for c in candidates:
        mine = list(lat[c].values())
        entry: dict[str, Any] = {
            "label": key["labels"].get(c),
            "ratings": {d: {"mean": round(statistics.mean(v), 2), "n": len(v)} if v else {"mean": None, "n": 0}
                        for d, v in ratings[c].items()},
            "status": status[c],
            "latency_ms": {"median": round(statistics.median(mine)), "p95": round(_p95(mine)), "n": len(mine)} if mine else None,
        }
        if c != baseline:
            deltas = [lat[c][s] - lat[baseline][s] for s in lat[c] if s in lat[baseline]]
            if deltas:
                med, p95 = statistics.median(deltas), _p95(deltas)
                entry["latency_vs_baseline"] = {
                    "pairs": len(deltas), "median_ms": round(med), "p95_ms": round(p95),
                    "meets_median_target": med <= TARGET_MEDIAN_MS, "meets_p95_target": p95 <= TARGET_P95_MS,
                }
            else:
                entry["latency_vs_baseline"] = None
        out["candidates"][c] = entry
    return out


def render(result: dict[str, Any], key: dict[str, Any]) -> str:
    names = {j["candidate"]: j["candidate_name"] for j in key["jobs"]}
    lines = ["Ratings (mean of 1 to 5, n in brackets)", ""]
    head = "| candidate | " + " | ".join(DIMENSIONS) + " |"
    lines += [head, "|" + "---|" * (len(DIMENSIONS) + 1)]
    for c, e in result["candidates"].items():
        cells = [f"{e['ratings'][d]['mean']} ({e['ratings'][d]['n']})" if e["ratings"][d]["n"] else "-" for d in DIMENSIONS]
        lines.append(f"| {c} {names[c][:48]} | " + " | ".join(cells) + " |")
    lines += ["", "Measured first-audio latency (ms), runner measurement; difference is paired with N0 on the same sentence/take", ""]
    for c, e in result["candidates"].items():
        lat = e["latency_ms"]
        base = e.get("latency_vs_baseline")
        text = f"  {c}: " + (f"median {lat['median']}, p95 {lat['p95']} (n={lat['n']})" if lat else "no latency data")
        if base:
            text += (f"; vs N0 ({base['pairs']} pairs): median {base['median_ms']:+d} ms "
                     f"[{'meets' if base['meets_median_target'] else 'MISSES'} +{TARGET_MEDIAN_MS}], "
                     f"p95 {base['p95_ms']:+d} ms [{'meets' if base['meets_p95_target'] else 'MISSES'} +{TARGET_P95_MS}]")
        text += f"; clips {e['status']}"
        lines.append(text)
    if result["ignored_ratings"] or result["unknown_clips"]:
        lines.append(f"\nignored non-numeric/out-of-range cells: {result['ignored_ratings']}; "
                     f"sheet rows with an unknown clip id: {result['unknown_clips']}")
    lines.append("\nOne rater and a few clips per cell is an indication, not a statistical result.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--sheet", required=True, help="the rating sheet after the raters filled it in (CSV)")
    ap.add_argument("--baseline", default="N0")
    args = ap.parse_args(argv)
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in Path(args.ledger).read_text(encoding="utf-8").splitlines() if line.strip()]
    with open(args.sheet, encoding="utf-8", newline="") as fh:
        sheet = list(csv.DictReader(fh))
    result = analyze(key, ledger, sheet, args.baseline)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(render(result, key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
