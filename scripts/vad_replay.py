"""Offline LocalVAD replay harness (no network, no paid provider, no phone).

    python scripts/vad_replay.py make [--out .validation-vad] [--lead-rms 90]   # build a SYNTHETIC clip set
    python scripts/vad_replay.py run  [--out .validation-vad]                   # replay every clip, write the report
    python scripts/vad_replay.py run --dir path/to/real_clips                   # replay REAL recordings (see below)

Every clip is stored as raw mu-law 8 kHz (the Plivo wire format) and fed to the real
``apps.api.channels.voice.local_vad.LocalVAD`` in 160-byte (20 ms) frames, exactly as ``realtime_bridge`` does.
Nothing here changes runtime behaviour; the decision rules below only RESTATE what ``adapter.py`` does so they can be
swept over thresholds without touching production defaults.

``make`` is SYNTHETIC: speech is Windows SAPI (English David/Zira voices, offline), noise and "TV" are generated
signals, one proxy clip is the agent's own previously generated Hinglish voice. None of it is a real caller on a real
phone line. ``run`` prints that label with every number. For real recordings, put 8 kHz-convertible audio files plus a
``manifest.json`` (same fields as the synthetic one) in a directory and pass ``--dir``.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import random
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from apps.api.channels.voice import local_vad

RATE = 8000
FRAME_BYTES = 160
THRESHOLDS = (1.0, 1.5, 2.0)  # seconds
LEAD_SECONDS = 1.0  # room tone before the event, so the VAD's noise floor settles the way it does mid-call
TAIL_SECONDS = 0.5
CALLER_RMS = 2500.0  # assumed caller level after the phone network: UNVERIFIED, see the report's sensitivity run
SERVER_VAD_SILENCE_S = 1.0  # prod VOICE_VAD_SILENCE_MS: the commit lands this long after the last speech


# --- mu-law ----------------------------------------------------------------------------------------------------------------


def lin2ulaw(sample: int) -> int:
    """G.711 mu-law encode of one 16-bit sample."""
    bias, clip = 0x84, 32635
    sign = 0x80 if sample < 0 else 0
    magnitude = min(clip, abs(sample)) + bias
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not magnitude & mask:
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def encode_ulaw(samples: list[int]) -> bytes:
    return bytes(lin2ulaw(max(-32768, min(32767, int(s)))) for s in samples)


# --- signal helpers (stdlib only) ------------------------------------------------------------------------------------------


def rms(samples: list[float]) -> float:
    return math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0.0


def scale_to(samples: list[float], target_rms: float) -> list[float]:
    current = rms(samples)
    return [x * target_rms / current for x in samples] if current else list(samples)


def silence(seconds: float) -> list[float]:
    return [0.0] * int(RATE * seconds)


def white(seconds: float, level: float, rnd: random.Random) -> list[float]:
    return [rnd.gauss(0, level) for _ in range(int(RATE * seconds))]


def pink_ish(seconds: float, level: float, rnd: random.Random) -> list[float]:
    """Low-passed noise (fan / air handler / distant traffic): one-pole filter, then scaled to ``level`` rms."""
    out, y = [], 0.0
    for _ in range(int(RATE * seconds)):
        y = 0.97 * y + rnd.gauss(0, 1)
        out.append(y)
    return scale_to(out, level)


def hum(seconds: float, level: float) -> list[float]:
    n = int(RATE * seconds)
    out = [
        math.sin(2 * math.pi * 120 * i / RATE) + 0.6 * math.sin(2 * math.pi * 240 * i / RATE)
        + 0.3 * math.sin(2 * math.pi * 360 * i / RATE)
        for i in range(n)
    ]
    return scale_to(out, level)


def clicks(seconds: float, level: float, rnd: random.Random, every: float) -> list[float]:
    """Knocks / keyboard: short decaying bursts at roughly ``every`` seconds."""
    out = [0.0] * int(RATE * seconds)
    t = 0.1
    while t < seconds - 0.1:
        start = int(t * RATE)
        for i in range(int(0.03 * RATE)):
            if start + i < len(out):
                out[start + i] += rnd.gauss(0, 1) * math.exp(-i / (0.006 * RATE)) * level
        t += every * rnd.uniform(0.7, 1.3)
    return out


def music_bed(seconds: float, level: float) -> list[float]:
    n = int(RATE * seconds)
    chord = (220.0, 277.2, 329.6)
    out = [
        sum(math.sin(2 * math.pi * f * i / RATE) for f in chord) * (0.6 + 0.4 * math.sin(2 * math.pi * 4 * i / RATE))
        for i in range(n)
    ]
    return scale_to(out, level)


def mix(*tracks: list[float]) -> list[float]:
    n = max(len(t) for t in tracks)
    return [sum(t[i] for t in tracks if i < len(t)) for i in range(n)]


def delay(track: list[float], seconds: float) -> list[float]:
    return silence(seconds) + track


# --- SAPI speech (offline, English voices) ---------------------------------------------------------------------------------


def sapi(text: str, voice: str, rate: int = 0) -> list[float]:
    """Synthesize ``text`` with the local Windows voice and return 8 kHz mono samples. Offline, free."""
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "s.wav"
        script = Path(tmp) / "s.ps1"
        script.write_text(
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            f"$s.SelectVoice('Microsoft {voice} Desktop')\n$s.Rate = {rate}\n"
            f"$s.SetOutputToWaveFile('{wav_path}')\n$s.Speak(@'\n{text}\n'@)\n$s.Dispose()\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            check=True, capture_output=True,
        )
        return load_audio(wav_path)


def load_audio(path: Path) -> list[float]:
    """Any audio file -> 8 kHz mono float samples via ffmpeg (local; nothing is uploaded)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-"],
        check=True, capture_output=True,
    ).stdout
    return [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw) - 1, 2)]


def trim_silence(samples: list[float], thresh: float = 150.0) -> list[float]:
    idx = [i for i, x in enumerate(samples) if abs(x) > thresh]
    return samples[idx[0] : idx[-1] + 1] if idx else samples


def speech(text: str, voice: str, rate: int = 0, level: float = CALLER_RMS) -> list[float]:
    return scale_to(trim_silence(sapi(text, voice, rate)), level)


def phrases(parts: list[str], voice: str, gap: float, rate: int, level: float = CALLER_RMS) -> list[float]:
    out: list[float] = []
    for i, part in enumerate(parts):
        out += speech(part, voice, rate, level) + (silence(gap) if i < len(parts) - 1 else [])
    return out


# --- clip set ------------------------------------------------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
AGENT_VOICE_PROXY = REPO / ".validation-voice-ab" / "labeled" / "C1_flash_baseline__hinglish_mixed.wav"


def build_clips(lead_rms: float, rnd: random.Random) -> list[dict]:
    """(name, category, should_interrupt, source, event samples). Event = the part a person would call 'the sound'."""
    C = CALLER_RMS
    clips: list[tuple[str, str, bool, str, list[float]]] = []

    def add(name: str, cat: str, interrupt: bool, source: str, event: list[float]) -> None:
        clips.append((name, cat, interrupt, source, event))

    # short acknowledgements
    add("ack_short_okay", "ack_short", False, "synthetic-sapi", speech("Okay.", "David"))
    add("ack_short_hmm", "ack_short", False, "synthetic-sapi", speech("Hmm.", "Zira"))
    add("ack_short_haan_ji", "ack_short", False, "synthetic-sapi", speech("Haan ji.", "David"))
    # long acknowledgements (the known exposure)
    add("ack_long_okay_okay", "ack_long", False, "synthetic-sapi", phrases(["Okay.", "Okay."], "Zira", 0.35, -3))
    add(
        "ack_long_haan_theek_achha", "ack_long", False, "synthetic-sapi",
        phrases(["Haan ji.", "Theek hai.", "Achha."], "David", 0.5, -3),
    )
    add(
        "ack_long_chain", "ack_long", False, "synthetic-sapi",
        phrases(["Haan haan.", "Theek hai ji.", "Achha achha.", "Samajh gaya."], "Zira", 0.4, -2),
    )
    # genuine interruptions
    add("genuine_wait_question", "genuine", True, "synthetic-sapi", speech("Wait, I have a question.", "David"))
    add("genuine_something_else", "genuine", True, "synthetic-sapi", speech("Actually, tell me something else.", "Zira"))
    add("genuine_price", "genuine", True, "synthetic-sapi", speech("What is the price of the franchise?", "David"))
    add("genuine_not_asked", "genuine", True, "synthetic-sapi", speech("No no, that is not what I asked.", "Zira"))
    add("genuine_quiet_wait", "genuine", True, "synthetic-sapi", speech("Wait, I have a question.", "Zira", 0, 700.0))
    if AGENT_VOICE_PROXY.exists():
        add(
            "genuine_hinglish_proxy", "genuine", True, "agent-voice-proxy (ElevenLabs, generated earlier, not a caller)",
            scale_to(trim_silence(load_audio(AGENT_VOICE_PROXY)), C),
        )
    # background conversation (other people in the room)
    two = lambda level: mix(  # noqa: E731
        speech("Did you call the electrician yesterday?", "David", 0, level),
        delay(speech("No, he said he will come tomorrow morning.", "Zira", 0, level), 1.6),
    )
    add("bg_conv_far", "bg_conversation", False, "synthetic-sapi", two(C * 0.25))
    add("bg_conv_near", "bg_conversation", False, "synthetic-sapi", two(C * 0.6))
    add("bg_conv_loud", "bg_conversation", False, "synthetic-sapi", two(C * 1.0))
    # television
    tv = speech("Good evening. Here are the top stories this hour. Markets closed higher today.", "David", -1, C * 0.6)
    add("tv_news", "tv", False, "synthetic-sapi+signal", mix(tv, music_bed(len(tv) / RATE, C * 0.15)))
    tv2 = speech("And that is why we always say, the best things in life take time.", "Zira", 0, C)
    add("tv_loud", "tv", False, "synthetic-sapi+signal", mix(tv2, music_bed(len(tv2) / RATE, C * 0.3), pink_ish(len(tv2) / RATE, 300, rnd)))
    # room noise / non-speech
    add("noise_fan", "noise", False, "synthetic-signal", pink_ish(3.0, 250, rnd))
    add("noise_hum", "noise", False, "synthetic-signal", hum(3.0, 300))
    add("noise_keyboard_knocks", "noise", False, "synthetic-signal", clicks(3.0, 3000, rnd, 0.25))
    add("noise_traffic_swell", "noise", False, "synthetic-signal", [x * (0.2 + 0.8 * i / (3 * RATE)) for i, x in enumerate(pink_ish(3.0, 900, rnd))])
    add("noise_loud_steady", "noise", False, "synthetic-signal", pink_ish(3.0, 700, rnd))

    out = []
    for name, cat, interrupt, source, event in clips:
        lead = white(LEAD_SECONDS, lead_rms, rnd)
        floor_noise = white(len(event) / RATE + TAIL_SECONDS, lead_rms, rnd)  # room tone continues under everything
        body = mix(event + silence(TAIL_SECONDS), floor_noise)
        samples = lead + body
        out.append(
            {
                "name": name, "category": cat, "should_interrupt": interrupt, "source": source,
                "event_start_s": LEAD_SECONDS, "event_end_s": LEAD_SECONDS + len(event) / RATE,
                "samples": samples,
            }
        )
    return out


def cmd_make(args: argparse.Namespace) -> int:
    out = Path(args.out) / "synthetic"
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(1234)  # fixed seed: the set is reproducible
    manifest = []
    for clip in build_clips(args.lead_rms, rnd):
        (out / f"{clip['name']}.ulaw").write_bytes(encode_ulaw(clip.pop("samples")))
        clip["file"] = f"{clip['name']}.ulaw"
        clip["lead_room_tone_rms"] = args.lead_rms
        clip["caller_rms_assumed"] = CALLER_RMS
        manifest.append(clip)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"wrote {len(manifest)} SYNTHETIC clips to {out}")
    return 0


# --- replay + decision rules -------------------------------------------------------------------------------------------------


def replay(ulaw: bytes, event_start: float, event_end: float) -> dict:
    """Feed the clip through LocalVAD frame by frame and record what each consumer in adapter.py would see."""
    vad = local_vad.LocalVAD()
    native_cut_at: float | None = None
    max_run = 0.0
    lead_false_cut = False
    started = False
    at_end: dict | None = None
    for i in range(len(ulaw) // FRAME_BYTES):
        t = i * 0.02
        if not started and t >= event_start:
            vad.mark_utterance()  # _begin_live_turn: a new caller turn begins
            started = True
        vad.feed(base64.b64encode(ulaw[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]).decode())
        if t < event_start and vad.sustained(0.4):
            lead_false_cut = True
        if started and t <= event_end:
            max_run = max(max_run, vad.run_seconds)
            if native_cut_at is None and vad.sustained(0.4):  # NATIVE_BARGE_IN_MIN_SPEECH_SECONDS
                native_cut_at = t - event_start
        if started and t >= event_end and at_end is None:
            at_end = {
                "utt_seconds": round(vad.utt_seconds, 2),
                "utt_mean_prob": round(vad.utt_mean_prob, 3),
                "confident": {str(th): vad.utterance_confident(th) for th in THRESHOLDS},
            }
    return {
        "native_cut_latency_s": None if native_cut_at is None else round(native_cut_at, 2),
        "max_run_s": round(max_run, 2),
        "lead_in_false_cut": lead_false_cut,
        **(at_end or {"utt_seconds": 0.0, "utt_mean_prob": 0.0, "confident": {}}),
    }


def decisions(duration: float, observed: dict) -> dict[str, bool]:
    """Would each commit-time rule cut the agent for this turn (text-less, worst case)?

    no_vad@T   : adapter without LocalVAD (production today): speech >= T          (shipping value 2.0)
    vad@T      : speech >= T and >= 1.0 s of confident frames                     (shipping value T=1.5)
    conf@T     : >= T s of confident frames alone
    native     : sustained(0.4) at any point during the event (native barge-in gate; the transcript check that
                 precedes it in adapter.py is NOT modelled: this is the energy gate's own verdict)
    """
    d: dict[str, bool] = {"native": observed["native_cut_latency_s"] is not None}
    for th in THRESHOLDS:
        d[f"no_vad@{th}"] = duration >= th
        d[f"vad@{th}"] = duration >= th and observed["confident"].get("1.0", False)
        d[f"conf@{th}"] = observed["confident"].get(str(th), False)
    return d


def cmd_run(args: argparse.Namespace) -> int:
    base = Path(args.dir) if args.dir else Path(args.out) / "synthetic"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    real = bool(args.dir)
    label = "REAL RECORDINGS" if real else "SYNTHETIC CLIPS (not real callers)"
    rows = []
    for clip in manifest:
        ulaw = (base / clip["file"]).read_bytes()
        duration = clip["event_end_s"] - clip["event_start_s"]
        obs = replay(ulaw, clip["event_start_s"], clip["event_end_s"])
        rows.append({**{k: clip[k] for k in ("name", "category", "should_interrupt", "source")}, "duration_s": round(duration, 2), **obs, "decisions": decisions(duration, obs)})

    rules = list(rows[0]["decisions"])
    neg = [r for r in rows if not r["should_interrupt"]]
    pos = [r for r in rows if r["should_interrupt"]]
    summary = {}
    for rule in rules:
        fp = [r["name"] for r in neg if r["decisions"][rule]]
        fn = [r["name"] for r in pos if not r["decisions"][rule]]
        summary[rule] = {
            "false_interruption_rate": round(len(fp) / len(neg), 3), "false_interruptions": fp,
            "missed_genuine_rate": round(len(fn) / len(pos), 3), "missed_genuine": fn,
        }
    result = {"label": label, "clips": len(rows), "rows": rows, "summary": summary}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = "real" if real else "synthetic"
    (out / f"results_{tag}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")

    print(f"== {label}: {len(rows)} clips ({len(neg)} must-not-cut, {len(pos)} genuine) ==")
    print(f"{'clip':<28}{'cat':<16}{'dur':>5}{'utt_s':>7}{'mean':>6}{'run':>6}{'native':>8}  cuts@")
    for r in rows:
        cuts = ",".join(k for k, v in r["decisions"].items() if v and k != "native")
        nat = "-" if r["native_cut_latency_s"] is None else f"{r['native_cut_latency_s']:.2f}"
        print(f"{r['name']:<28}{r['category']:<16}{r['duration_s']:>5}{r['utt_seconds']:>7}{r['utt_mean_prob']:>6}{r['max_run_s']:>6}{nat:>8}  {cuts}")
    print(f"\n{'rule':<12}{'false-cut rate':>16}{'missed genuine':>16}")
    for rule, s in summary.items():
        print(f"{rule:<12}{s['false_interruption_rate']:>16.0%}{s['missed_genuine_rate']:>16.0%}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    make = sub.add_parser("make")
    make.add_argument("--out", default=".validation-vad")
    make.add_argument("--lead-rms", type=float, default=90.0, help="room-tone level under every clip (assumed)")
    run = sub.add_parser("run")
    run.add_argument("--out", default=".validation-vad")
    run.add_argument("--dir", help="directory with real clips + manifest.json (default: the synthetic set)")
    args = parser.parse_args(argv)
    return cmd_make(args) if args.cmd == "make" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
