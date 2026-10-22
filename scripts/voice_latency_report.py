"""Summarise voice latency from the API's JSON logs.

Reads structlog JSON lines (one object per line) from files or stdin and prints
n / p50 / p95 / p99 for every `voice_turn_latency` segment, the interruption
timings and the estimated playback tail. Non-JSON lines and other events are
ignored. No transcripts or phone numbers are read or printed.

    docker logs veerox-api-1 2>&1 | python scripts/voice_latency_report.py
    python scripts/voice_latency_report.py api.log other.log

Percentiles are nearest-rank and are only as good as `n`: a handful of turns
from one call says almost nothing about the fleet.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Iterator

from apps.api.channels.voice.latency import summarize_events, turn_event_counts

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def iter_events(lines: Iterable[str]) -> Iterator[dict]:
    for line in lines:
        line = _ANSI.sub("", line).strip()
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def format_report(summary: dict[str, dict]) -> str:
    if not summary:
        return "No voice latency events found."
    rows = [f"{'metric':<38}{'n':>6}{'p50':>9}{'p95':>9}{'p99':>9}   (ms)"]
    for name, s in summary.items():
        cells = ["-" if s[k] is None else str(round(s[k])) for k in ("p50", "p95", "p99")]
        rows.append(f"{name:<38}{s['n']:>6}{cells[0]:>9}{cells[1]:>9}{cells[2]:>9}")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    if argv:
        lines: Iterable[str] = (ln for path in argv for ln in open(path, encoding="utf8", errors="replace"))
    else:
        lines = sys.stdin
    events = list(iter_events(lines))
    print(format_report(summarize_events(events)))
    extra = turn_event_counts(events)
    if extra["counts"]:
        print()
        print(f"turn events over {extra['turns']} turns (counts of suspected/prevented cases, not measured quality):")
        for kind, n in sorted(extra["counts"].items()):
            print(f"  {kind:<34}{n:>6}  ({extra['per_100_turns'].get(kind, '-')} per 100 turns)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
