"""In-process counters for the decision layer: how long a decision took and how
often it fell back, by reason. Observational only (nothing reads these to make
a decision), bounded, and reset on restart; ``GET /diag/voice-latency`` shows
them next to the voice latency percentiles. Latency percentiles use the same
nearest-rank method as ``channels/voice/latency.py`` and always carry ``n``.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from typing import Any

_WINDOW = 1000
_latencies: dict[str, deque[float]] = {}
_outcomes: dict[str, Counter[str]] = {}


def record(channel: str, outcome: str, latency_ms: float | None = None) -> None:
    """One decision: ``outcome`` is "ok" or a fallback reason ("timeout",
    "low_confidence", "budget_timeout", ...). Never raises."""
    try:
        _outcomes.setdefault(channel, Counter())[outcome] += 1
        if latency_ms is not None:
            _latencies.setdefault(channel, deque(maxlen=_WINDOW)).append(float(latency_ms))
    except Exception:  # noqa: BLE001 - metrics must never touch a call
        return


def _percentile(data: list[float], q: float) -> float | None:
    if not data:
        return None
    ordered = sorted(data)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for channel in sorted(set(_outcomes) | set(_latencies)):
        counts = _outcomes.get(channel, Counter())
        total = sum(counts.values())
        data = list(_latencies.get(channel, ()))
        out[channel] = {
            "decisions": total,
            "outcomes": dict(counts),
            # "smart_script" is a deliberate hand-off to the org's script, not a failure.
            "fallback_rate": round(1 - (counts.get("ok", 0) + counts.get("smart_script", 0)) / total, 4)
            if total
            else None,
            "latency_ms": {
                "n": len(data),
                "p50": _percentile(data, 50),
                "p95": _percentile(data, 95),
                "p99": _percentile(data, 99),
            },
        }
    return out


def reset() -> None:
    _latencies.clear()
    _outcomes.clear()
