"""Voice quality comparison: rating sheets and aggregation for human listening tests.

Nothing here judges audio. It generates a blind rating sheet (variant names hidden behind random labels),
reads back the scores listeners gave, and reports per-variant means - and only names a leader when there are
enough ratings from enough different listeners and the gap is larger than the noise. With too little data the
answer is "no conclusion", by design: a verdict must come from real listening, never from this module.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

VARIANTS = ("elevenlabs_turbo_v2_5", "elevenlabs_flash_v2_5", "openai_native")
CRITERIA = ("naturalness", "clarity", "hindi_pronunciation", "name_pronunciation", "pacing", "interruption_feel")
SCALE = (1, 5)
MIN_RATERS = 3
MIN_RATINGS_PER_VARIANT = 12
MIN_GAP = 0.3  # mean-score difference (on a 1-5 scale) below which two variants are not told apart


def make_sheet(prompts: Sequence[str], variants: Sequence[str] = VARIANTS, seed: int | None = None) -> list[dict[str, Any]]:
    """One row per (prompt, variant) in shuffled order with an opaque clip label. ``key`` maps labels back to
    variants and stays with the organiser; raters only see ``label`` and ``prompt``."""
    rng = random.Random(seed)
    rows = [{"prompt": p, "variant": v} for p in prompts for v in variants]
    rng.shuffle(rows)
    return [{"label": f"clip-{i + 1:03d}", **row} for i, row in enumerate(rows)]


def rater_view(sheet: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a listener receives: no variant names."""
    return [{"label": r["label"], "prompt": r["prompt"], "scores": {c: None for c in CRITERIA}} for r in sheet]


def validate_rating(row: Mapping[str, Any]) -> list[str]:
    problems = []
    for criterion, score in (row.get("scores") or {}).items():
        if criterion not in CRITERIA:
            problems.append(f"unknown criterion {criterion!r}")
        elif score is None:
            continue
        elif not isinstance(score, (int, float)) or isinstance(score, bool) or not SCALE[0] <= score <= SCALE[1]:
            problems.append(f"{criterion}: {score!r} is not a score from {SCALE[0]} to {SCALE[1]}")
    if not row.get("rater"):
        problems.append("missing rater")
    return problems


def aggregate(sheet: Sequence[Mapping[str, Any]], ratings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """``ratings``: ``{"rater": str, "label": str, "scores": {criterion: 1-5 or None}}``. Invalid rows are
    reported and ignored. Returns per-variant, per-criterion means plus a conservative conclusion."""
    variant_of = {r["label"]: r["variant"] for r in sheet}
    values: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    raters: dict[str, set[str]] = defaultdict(set)
    ignored: list[str] = []
    for row in ratings:
        problems = validate_rating(row)
        variant = variant_of.get(row.get("label"))
        if problems or variant is None:
            ignored.append(f"{row.get('rater')}/{row.get('label')}: {'; '.join(problems) or 'unknown clip label'}")
            continue
        for criterion, score in (row.get("scores") or {}).items():
            if score is not None:
                values[variant][criterion].append(float(score))
                raters[variant].add(str(row["rater"]))
    variants: dict[str, Any] = {}
    for variant, by_criterion in values.items():
        flat = [s for scores in by_criterion.values() for s in scores]
        variants[variant] = {
            "n_scores": len(flat),
            "raters": len(raters[variant]),
            "mean": round(statistics.fmean(flat), 2) if flat else None,
            "by_criterion": {c: round(statistics.fmean(s), 2) for c, s in by_criterion.items()},
        }
    return {"variants": variants, "ignored": ignored, "conclusion": conclude(variants)}


def conclude(variants: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Name a leader only with enough data and a clear gap; otherwise say why not."""
    if len(variants) < 2:
        return {"leader": None, "reason": "fewer than two variants have ratings"}
    thin = {
        v: s for v, s in variants.items() if s["raters"] < MIN_RATERS or s["n_scores"] < MIN_RATINGS_PER_VARIANT
    }
    if thin:
        return {
            "leader": None,
            "reason": f"not enough ratings (need {MIN_RATERS}+ raters and {MIN_RATINGS_PER_VARIANT}+ scores per variant); short: {sorted(thin)}",
        }
    ranked = sorted(variants.items(), key=lambda kv: kv[1]["mean"], reverse=True)
    gap = ranked[0][1]["mean"] - ranked[1][1]["mean"]
    if gap < MIN_GAP:
        return {"leader": None, "reason": f"top two are within {MIN_GAP} points ({gap:.2f}); no difference can be claimed"}
    return {"leader": ranked[0][0], "reason": f"highest mean by {gap:.2f} points over {ranked[1][0]}; a listening result, not a guarantee"}
