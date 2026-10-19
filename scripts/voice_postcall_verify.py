"""Run the automated post-call checks for one validation scenario on exported data.

    python scripts/voice_postcall_verify.py S04 --logs api.log --call-uuid <uuid> \
        [--conversation conv.json] [--appointments appts.json] [--usage usage.json]

Reads JSON log lines (filtered to the call), optional exported records, and prints pass / fail / manual /
skipped per check using scripts/voice_validation_scenarios.json. Read-only: no network, no database.
Exit status 1 if any check failed, so it can gate a pilot step.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from apps.api.channels.voice import call_verify

CATALOG = Path(__file__).with_name("voice_validation_scenarios.json")


def _json(path: str | None):
    return json.loads(Path(path).read_text(encoding="utf8")) if path else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario")
    parser.add_argument("--logs", required=True)
    parser.add_argument("--call-uuid", required=True)
    parser.add_argument("--conversation")
    parser.add_argument("--appointments")
    parser.add_argument("--usage")
    args = parser.parse_args(argv)

    scenarios = {s["id"]: s for s in json.loads(CATALOG.read_text(encoding="utf8"))["scenarios"]}
    if args.scenario not in scenarios:
        print(f"unknown scenario {args.scenario}; choose from {sorted(scenarios)}", file=sys.stderr)
        return 2
    scenario = scenarios[args.scenario]
    raw = Path(args.logs).read_text(encoding="utf8", errors="replace")
    lines = [ln for ln in raw.splitlines() if args.call_uuid in ln]
    events = []
    for ln in lines:
        start = ln.find("{")
        try:
            event = json.loads(ln[start:]) if start >= 0 else None
        except ValueError:
            event = None
        if isinstance(event, dict):
            events.append(event)
    results = call_verify.verify_call(
        events, scenario["auto_checks"] + scenario["manual_checks"],
        conversation=_json(args.conversation), appointments=_json(args.appointments), usage_rows=_json(args.usage),
        raw_lines="\n".join(lines), params=scenario.get("params"),
    )
    for r in results:
        print(f"{r.status.upper():8} {r.id:24} {r.detail}")
    return 1 if any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
