"""Local speech-probability gate for the barge-in guard (``VOICE_LOCAL_VAD``).

Reads the caller's mu-law 8 kHz frames, scores each 20 ms frame with a probability of speech, and reports
whether the caller has been speaking *confidently and continuously* for long enough to justify cutting the
agent off. Backchannels ("hmm", "acha") are short and soft, so they never reach that bar; a real interruption
is louder and longer.

This is an energy-over-adaptive-noise-floor classifier written in numpy, not Silero: ``onnxruntime`` has no wheel
for this Python and torch would more than double the image. ``score_frame`` is the one place to swap in a Silero
call later. It cannot tell "hmm" from a word by spectrum; the transcript check in adapter.py still does that.

UNVERIFIED on live calls: the thresholds are starting points to tune from ``voice_vad_gate`` log lines.
"""

from __future__ import annotations

import base64

import numpy as np

FRAME_SAMPLES = 160  # 20 ms at 8 kHz
FRAME_SECONDS = 0.02
SPEECH_PROB = 0.70  # a frame at or above this counts as speech
CONFIDENT_PROB = 0.85  # mean over the run must reach this to cut
_SNR_FLOOR_DB = 8.0  # dB over the noise floor where the probability starts rising
_SNR_SPAN_DB = 10.0  # ... and reaches 1.0 this many dB later
_MIN_RMS = 60.0  # below this a frame is silence whatever the noise floor says


def _build_ulaw_table() -> np.ndarray:
    u = (~np.arange(256, dtype=np.int32)) & 0xFF
    sign = u & 0x80
    exponent = (u >> 4) & 0x07
    mantissa = u & 0x0F
    magnitude = (((mantissa << 3) + 0x84) << exponent) - 0x84
    return np.where(sign != 0, -magnitude, magnitude).astype(np.float32)


_ULAW = _build_ulaw_table()


def decode_ulaw(payload_b64: str) -> np.ndarray:
    return _ULAW[np.frombuffer(base64.b64decode(payload_b64), dtype=np.uint8)]


class LocalVAD:
    """Feed it each caller media payload; ask ``sustained()`` when deciding whether to cut."""

    def __init__(self) -> None:
        self._carry = np.zeros(0, dtype=np.float32)
        self._noise_db = 40.0  # adapts down quickly, up slowly, so speech does not raise the floor
        self.run_frames = 0
        self._run_prob_sum = 0.0
        self.last_prob = 0.0

    def score_frame(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame * frame))) if frame.size else 0.0
        if rms < _MIN_RMS:
            self._noise_db = min(self._noise_db, 20.0 * np.log10(max(rms, 1.0)))
            return 0.0
        db = 20.0 * np.log10(rms)
        rate = 0.2 if db < self._noise_db else 0.002
        self._noise_db += (db - self._noise_db) * rate
        return float(np.clip((db - self._noise_db - _SNR_FLOOR_DB) / _SNR_SPAN_DB + 0.7, 0.0, 1.0))

    def feed(self, payload_b64: str) -> None:
        try:
            samples = np.concatenate([self._carry, decode_ulaw(payload_b64)])
        except Exception:  # noqa: BLE001 - a bad frame must never touch the call
            return
        n = len(samples) // FRAME_SAMPLES
        for i in range(n):
            prob = self.score_frame(samples[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES])
            self.last_prob = prob
            if prob >= SPEECH_PROB:
                self.run_frames += 1
                self._run_prob_sum += prob
            else:
                self.run_frames = 0
                self._run_prob_sum = 0.0
        self._carry = samples[n * FRAME_SAMPLES :]

    @property
    def run_seconds(self) -> float:
        return self.run_frames * FRAME_SECONDS

    @property
    def run_mean_prob(self) -> float:
        return self._run_prob_sum / self.run_frames if self.run_frames else 0.0

    def sustained(self, min_seconds: float) -> bool:
        """Continuous speech for ``min_seconds`` with a mean probability above the confidence bar."""
        return self.run_seconds >= min_seconds and self.run_mean_prob >= CONFIDENT_PROB
