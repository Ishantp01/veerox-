"""scripts/vad_replay.py: the offline LocalVAD replay harness (stdlib signals only; no SAPI, ffmpeg or network)."""

from __future__ import annotations

import importlib.util
import math
import random
from pathlib import Path

from apps.api.channels.voice import local_vad

_spec = importlib.util.spec_from_file_location("vad_replay", Path(__file__).parents[3] / "scripts" / "vad_replay.py")
assert _spec and _spec.loader
vr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vr)


def test_ulaw_encoding_round_trips_through_the_runtime_decoder() -> None:
    for sample in (-20000, -1000, -50, 0, 50, 1000, 20000):
        decoded = local_vad._ULAW[vr.lin2ulaw(sample)]
        assert abs(decoded - sample) <= max(40, abs(sample) * 0.05)


def _clip(event: list[float]) -> bytes:
    rnd = random.Random(1)
    lead = vr.white(1.0, 90, rnd)
    return vr.encode_ulaw(lead + vr.mix(event + vr.silence(0.5), vr.white(len(event) / vr.RATE + 0.5, 90, rnd)))


def test_loud_speechlike_event_is_confident_and_cut_by_the_native_gate() -> None:
    event = [vr.CALLER_RMS * 1.4 * math.sin(2 * math.pi * 220 * i / vr.RATE) for i in range(vr.RATE * 2)]
    obs = vr.replay(_clip(event), 1.0, 3.0)
    assert obs["native_cut_latency_s"] is not None and obs["native_cut_latency_s"] < 0.6
    assert obs["confident"]["1.5"] and not obs["lead_in_false_cut"]
    assert vr.decisions(2.0, obs)["vad@1.5"]


def test_a_short_blip_and_silence_are_not_cut() -> None:
    blip = [vr.CALLER_RMS * 1.4 * math.sin(2 * math.pi * 220 * i / vr.RATE) for i in range(int(vr.RATE * 0.2))]
    obs = vr.replay(_clip(blip), 1.0, 1.2)
    assert obs["native_cut_latency_s"] is None
    d = vr.decisions(0.2, obs)
    assert not any(d.values())
    assert not any(vr.decisions(0.0, vr.replay(_clip(vr.silence(1.0)), 1.0, 2.0)).values())
