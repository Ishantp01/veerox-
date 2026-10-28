# ruff: noqa: E501  (table formatting)
r"""Objective measurements of voice recordings, to compare WHERE a voice's sound changes between stages.

    python scripts\voice_audio_metrics.py a_preview.mp3 b_api.mp3 c_veerox_ulaw.wav d_phone.wav

Offline and free: it only reads the files you give it (WAV, MP3, anything ffmpeg decodes; raw mu-law needs a name ending
in .ulaw). It uses ffmpeg only (no numpy), prints a table, and never uploads or changes anything. These numbers describe
bandwidth, loudness and brightness; they say nothing about accent, pronunciation or naturalness, which need listening.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BANDS = [("0-300", 0, 300), ("300-1k", 300, 1000), ("1-2k", 1000, 2000), ("2-3.4k", 2000, 3400), ("3.4-4k", 3400, 4000),
         ("4-8k", 4000, 8000), (">8k", 8000, 20000)]


def _run(args: list[str]) -> str:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stderr + result.stdout


def _input(path: Path) -> list[str]:
    return ["-f", "mulaw", "-ar", "8000", "-ac", "1", "-i", str(path)] if path.suffix == ".ulaw" else ["-i", str(path)]


def probe(path: Path) -> dict:
    if path.suffix == ".ulaw":
        return {"rate": 8000, "channels": 1, "seconds": path.stat().st_size / 8000, "codec": "pcm_mulaw"}
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return {"rate": int(stream["sample_rate"]), "channels": int(stream["channels"]), "seconds": float(data["format"]["duration"]), "codec": stream["codec_name"]}


def loudness(path: Path) -> tuple[float | None, float | None]:
    text = _run([*_input(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
    integrated = re.findall(r"I:\s+(-?[\d.]+) LUFS", text)
    peak = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", text)
    return (float(integrated[-1]) if integrated else None, float(peak[-1]) if peak else None)


def band_levels(path: Path, nyquist: float) -> dict[str, float]:
    """RMS energy (linear power) of each band, so shares can be computed."""
    out = {}
    for name, lo, hi in BANDS:
        if lo >= nyquist:
            out[name] = 0.0
            continue
        chain = (f"highpass=f={lo}," if lo else "") + f"lowpass=f={min(hi, nyquist - 1)},astats=metadata=0"
        text = _run([*_input(path), "-af", chain, "-f", "null", "-"])
        levels = re.findall(r"RMS level dB:\s+(-?[\d.inf]+)", text)
        db = float(levels[-1]) if levels and levels[-1] not in ("-inf",) else -120.0
        out[name] = 10 ** (db / 10)
    return out


def spectral(path: Path) -> tuple[float | None, float | None]:
    text = _run([*_input(path), "-af", "aspectralstats=measure=centroid+rolloff,ametadata=mode=print:file=-", "-f", "null", "-"])
    cents = [float(x) for x in re.findall(r"aspectralstats\.1\.centroid=([\d.]+)", text)]
    rolls = [float(x) for x in re.findall(r"aspectralstats\.1\.rolloff=([\d.]+)", text)]
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    return mean(cents), mean(rolls)


def measure(path: Path) -> dict:
    info = probe(path)
    nyq = info["rate"] / 2
    lufs, peak = loudness(path)
    bands = band_levels(path, nyq)
    total = sum(bands.values()) or 1.0
    centroid, rolloff = spectral(path)
    return {"file": path.name, **info, "lufs": lufs, "peak": peak, "centroid": centroid, "rolloff": rolloff,
            "shares": {k: 100 * v / total for k, v in bands.items()}}


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    rows = [measure(Path(p)) for p in paths]
    print(f"{'file':44s}{'codec':10s}{'rate':>7s}{'sec':>6s}{'LUFS':>7s}{'peak':>7s}{'centroid':>9s}{'rolloff':>9s}")
    for r in rows:
        f = lambda v, fmt: "-" if v is None else format(v, fmt)  # noqa: E731
        print(f"{r['file'][:43]:44s}{r['codec'][:9]:10s}{r['rate']:>7d}{r['seconds']:>6.1f}{f(r['lufs'], '.1f'):>7s}{f(r['peak'], '.1f'):>7s}"
              f"{f(r['centroid'], '.0f'):>9s}{f(r['rolloff'], '.0f'):>9s}")
    print()
    print("share of the signal's energy per frequency band (%) - what each file keeps:")
    print(f"{'file':44s}" + "".join(f"{name:>8s}" for name, _, _ in BANDS))
    for r in rows:
        print(f"{r['file'][:43]:44s}" + "".join(f"{r['shares'][name]:>8.1f}" for name, _, _ in BANDS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
