"""Blind voice-quality rating sheets and results (apps/api/channels/voice/quality.py).

    python scripts/voice_quality_rate.py sheet prompts.txt --seed 7 --out sheet.json
        -> sheet.json (organiser key) and sheet.rater.json (give this one to listeners)
    python scripts/voice_quality_rate.py report sheet.json ratings.json

``prompts.txt``: one test sentence per line (cover Hindi, Hinglish, English, names, amounts, dates).
``ratings.json``: [{"rater": "A", "label": "clip-001", "scores": {"naturalness": 4, ...}}, ...]

This script makes no API calls and generates no audio. Producing the clips (Turbo v2.5, Flash v2.5, OpenAI
native) is a separate, paid step that needs approval; use scripts/voice_compare.py for that.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from apps.api.channels.voice import quality


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sheet = sub.add_parser("sheet")
    sheet.add_argument("prompts")
    sheet.add_argument("--seed", type=int)
    sheet.add_argument("--out", default="sheet.json")
    report = sub.add_parser("report")
    report.add_argument("sheet")
    report.add_argument("ratings")
    args = parser.parse_args(argv)

    if args.cmd == "sheet":
        prompts = [line.strip() for line in Path(args.prompts).read_text(encoding="utf8").splitlines() if line.strip()]
        rows = quality.make_sheet(prompts, seed=args.seed)
        out = Path(args.out)
        out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf8")
        out.with_suffix(".rater.json").write_text(json.dumps(quality.rater_view(rows), indent=1, ensure_ascii=False), encoding="utf8")
        print(f"{len(rows)} clips -> {out} (key) and {out.with_suffix('.rater.json')} (for listeners)")
        return 0
    rows = json.loads(Path(args.sheet).read_text(encoding="utf8"))
    ratings = json.loads(Path(args.ratings).read_text(encoding="utf8"))
    print(json.dumps(quality.aggregate(rows, ratings), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
