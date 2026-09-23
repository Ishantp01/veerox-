"""Live turn intelligence for voice calls.

While the agent is speaking and the caller talks over it, the adapter has to
decide — fast, in any language — whether that is a filler ("haan", "ok",
"I see") the agent should talk through, or a real turn (a question, request,
objection) it should stop for and answer.

Two pieces live here:

* ``LiveTranscriber`` — a second OpenAI Realtime connection (a transcription
  session running ``gpt-live-transcribe``) that receives a copy of the
  caller's audio and streams partial text back *while they are still
  speaking*. The main conversation session's own transcription
  (whisper-1) only produces text after the turn ends, which is too late.
* ``classify_partial`` / ``llm_is_real_turn`` — turn the partial text into a
  verdict. Cheap rules decide the obvious cases; a tiny LLM call decides the
  rest, so it works for languages nobody has written a word list for.

Everything here is best-effort: if the transcriber can't connect or drops
mid-call, ``healthy`` goes False and the adapter falls back to its
duration-based rules. Nothing in this module may break a call.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import structlog
import websockets

from apps.api.config import settings

logger = structlog.get_logger(__name__)

Verdict = Literal["filler", "real", "unsure"]

_REALTIME_TRANSCRIBE_URL = "wss://api.openai.com/v1/realtime?intent=transcription"

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

BACKCHANNEL_WORDS = frozenset(
    {
        "ok", "okay", "okk", "k", "kk", "hmm", "hm", "hmmm", "mm", "mmm", "mhm", "uh", "um",
        "uhh", "umm", "oh", "ohh", "ah", "aah", "haan", "han", "haa", "ha", "hanji", "haanji",
        "ji", "achha", "accha", "acha", "achcha", "theek", "thik", "tik", "hai", "sahi",
        "right", "yes", "yeah", "yep", "yup", "sure", "fine", "alright", "all", "good", "nice",
        "cool", "great", "got", "it", "i", "see", "understood", "bilkul", "samajh", "gaya",
        "gayi", "sir", "madam", "mam", "ma'am", "lovely", "perfect", "wow", "true", "correct",
        "अच्छा", "ठीक", "है", "हां", "हाँ", "हा", "जी", "हम्म", "ओके", "ओके।", "सही", "बिल्कुल",
        "समझ", "गया", "गई", "बढ़िया", "अच्छी", "बात",
        # A few common ones in other Indian languages; anything not listed is
        # judged by the LLM check instead, so this needn't be exhaustive.
        "ஆமா", "சரி", "புரிந்தது", "అవును", "సరే", "అర్థమైంది", "হ্যাঁ", "ঠিক", "আছে", "বুঝেছি",
        "હા", "બરાબર", "સમજાયું", "ಹೌದು", "ಸರಿ", "ಅರ್ಥವಾಯಿತು", "അതെ", "ശരി", "മനസ്സിലായി",
        "ਹਾਂ", "ਠੀਕ", "ਹੈ",
    }
)

# Words that signal the caller is asking or asking for something — enough to
# treat an utterance as a real turn without waiting on the LLM.
QUESTION_CUES = frozenset(
    {
        "kya", "kab", "kaise", "kaisa", "kitna", "kitne", "kitni", "kyun", "kyu", "kaun",
        "kahan", "kaha", "batao", "bataiye", "batana", "price", "cost", "pricing", "demo",
        "how", "what", "when", "why", "where", "who", "which", "wait", "stop", "hold",
        "ruko", "rukiye", "ruk", "suno", "suniye", "sunna", "but", "lekin", "magar", "par",
        "however", "actually", "question", "sawaal", "sawal", "problem", "nahi", "nahin",
        "no", "not", "don't", "dont", "can", "could", "would", "will", "please", "tell",
        "क्या", "कब", "कैसे", "कितना", "कितने", "कितनी", "क्यों", "कौन", "कहाँ", "कहां",
        "बताओ", "बताइए", "रुको", "रुकिए", "सुनो", "सुनिए", "लेकिन", "मगर", "पर", "सवाल",
        "नहीं", "कीमत", "डेमो",
    }
)

# Longer than this with anything beyond fillers is not a "haan haan, ok".
# Lowered from 8 so more real interruptions resolve instantly off the cheap
# rules instead of waiting on the (still fast, but non-zero) LLM check.
_LONG_UTTERANCE_WORDS = 5
_MAX_FILLER_WORDS = 6

_STRIP = " \t\n.,!?;:।|\"'“”‘’()-…¿？،؟"


def _tokens(text: str) -> list[str]:
    return [w for w in (t.strip(_STRIP) for t in text.lower().split()) if w]


def is_backchannel(text: str, max_words: int = 4) -> bool:
    """True for empty/noise transcripts and short filler acknowledgments
    ("ok", "theek hai", "accha", "haan ji") — not real questions."""
    words = _tokens(text)
    if not words:
        return True
    return len(words) <= max_words and all(w in BACKCHANNEL_WORDS for w in words)


def classify_partial(text: str) -> Verdict:
    """Cheap first-pass verdict on the (possibly partial, possibly garbled)
    transcript of what the caller is saying over the agent.

    "filler"  — only acknowledgment words: keep talking.
    "real"    — clearly a turn: a question/request cue, or a long utterance
                with real content: stop and answer.
    "unsure"  — anything else (other languages, garbled text): ask the LLM.

    A "?" is deliberately NOT treated as proof of a question — the
    transcriber sprinkles them on fillers in some languages.
    """
    words = _tokens(text)
    if not words:
        return "unsure"
    non_filler = [w for w in words if w not in BACKCHANNEL_WORDS]
    if not non_filler:
        return "filler" if len(words) <= _MAX_FILLER_WORDS else "real"
    if any(w in QUESTION_CUES for w in words):
        return "real"
    if len(words) >= _LONG_UTTERANCE_WORDS:
        return "real"
    return "unsure"


# ---------------------------------------------------------------------------
# Adaptive endpointing
# ---------------------------------------------------------------------------

# Words a speaker ends on when they are clearly about to say more.
CONTINUATION_WORDS = frozenset(
    {
        "and", "but", "so", "because", "then", "or", "like", "actually", "well", "um", "uh",
        "umm", "uhh", "the", "a", "an", "to", "of", "my", "i", "mean", "that", "if", "when",
        "aur", "lekin", "magar", "toh", "to", "kyunki", "ki", "matlab", "mera", "meri", "mujhe",
        "woh", "wo", "vo", "yaani", "basically", "jaise", "agar", "ya", "phir", "fir", "main",
        "और", "लेकिन", "मगर", "तो", "क्योंकि", "कि", "मतलब", "मेरा", "मेरी", "मुझे", "वो",
        "यानी", "जैसे", "अगर", "या", "फिर", "मैं",
    }
)
# Sentence-final punctuation from the transcriber (Latin, Devanagari danda,
# Arabic/Urdu question mark, full-width).
_TERMINAL = (".", "?", "!", "।", "؟", "？", "！", "。")
# How long to keep waiting for the caller to continue.
HOLD_CONTINUATION_SECONDS = 1.0
HOLD_LONE_FILLER_SECONDS = 0.6


def endpoint_hold_seconds(text: str, agent_was_busy: bool) -> float:
    """How much longer to wait before answering, given the text streamed so far
    for the caller's turn — 0 when the turn looks finished.

    Turn end is otherwise decided by a fixed 0.4s silence, which cuts off a
    caller who pauses mid-sentence ("haan… mera matlab…"). Streamed text lags
    the audio by up to ~1s, so this looks at a *prefix*: callers re-check it as
    more text arrives and release the hold as soon as it stops looking
    unfinished (see adapter.handle_live_delta).
    """
    stripped = text.strip()
    words = _tokens(stripped)
    if not words:
        return 0.0  # no text yet — don't guess, answer normally
    lone_filler = len(words) <= 2 and all(w in BACKCHANNEL_WORDS for w in words)
    if lone_filler and not agent_was_busy:
        # A bare "haan"/"ok" to an idle agent is very often followed by more.
        return HOLD_LONE_FILLER_SECONDS
    if stripped.endswith(_TERMINAL) and not stripped.endswith("..."):
        return 0.0
    if stripped.endswith(("…", "...", ",")) or words[-1] in CONTINUATION_WORDS:
        return HOLD_CONTINUATION_SECONDS
    return 0.0


# ---------------------------------------------------------------------------
# LLM turn judgement (any language)
# ---------------------------------------------------------------------------

TURN_LABELS = (
    "FILLER",
    "QUESTION",
    "CLARIFICATION",
    "OBJECTION",
    "INTERRUPTION",
    "ANSWER",
    "NEW_TOPIC",
    "CONTINUATION",
    "END",
)
# Below this the judgement is ignored and the agent keeps listening.
LLM_MIN_CONFIDENCE = 0.6


@dataclass(frozen=True)
class TurnJudgement:
    label: str
    confidence: float

    @property
    def verdict(self) -> Literal["filler", "real", "wait", "unsure"]:
        """filler: keep talking. real: stop and answer. wait: the caller is
        mid-sentence, judge again once more text arrives. unsure: low
        confidence — keep listening."""
        if self.confidence < LLM_MIN_CONFIDENCE:
            return "unsure"
        if self.label == "FILLER":
            return "filler"
        if self.label == "CONTINUATION":
            return "wait"
        return "real"


_LLM_SYSTEM = (
    "You classify one utterance from a caller during a phone call with an AI sales agent, "
    "spoken while the agent was still talking. The transcript is automatic, may be in any "
    "language (often Indian languages or Hinglish), and may be garbled, cut off, or in the "
    "wrong script. Choose exactly one label:\n"
    "FILLER - only an acknowledgment or reaction (ok, yes, I see, right, hmm, got it, nice, "
    "thanks, or the equivalent in any language) with no question, request or information.\n"
    "QUESTION - asks something.\n"
    "CLARIFICATION - asks the agent to repeat or explain what it just said.\n"
    "OBJECTION - pushes back, refuses, or says they are not interested.\n"
    "INTERRUPTION - asks the agent to stop, wait or listen.\n"
    "ANSWER - gives information or a choice.\n"
    "NEW_TOPIC - raises something unrelated to what the agent was saying.\n"
    "CONTINUATION - a sentence clearly cut off mid-way, more is coming.\n"
    "END - wants to end the call.\n"
    'Reply with only JSON: {"label": "<LABEL>", "confidence": <0 to 1>}. When unsure between '
    "FILLER and anything else, choose the other label."
)

_JSON_RE = re.compile(r"\{.*?\}", re.S)


def parse_judgement(content: str | None) -> TurnJudgement | None:
    if not content:
        return None
    match = _JSON_RE.search(content)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        label = str(data["label"]).strip().upper()
        confidence = float(data.get("confidence", 0.0))
    except (ValueError, KeyError, TypeError):
        return None
    if label not in TURN_LABELS:
        return None
    return TurnJudgement(label=label, confidence=max(0.0, min(1.0, confidence)))


async def judge_turn(text: str, api_key: str | None, timeout: float = 0.8) -> TurnJudgement | None:
    """Ask a small, fast model to label ``text`` (any language) with a
    confidence, or None if it couldn't answer in time."""
    from apps.api.core.llm import chat_completion  # local: keeps import cost off startup

    try:
        result = await asyncio.wait_for(
            chat_completion(
                [
                    {"role": "system", "content": _LLM_SYSTEM},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=30,
                api_key=api_key,
            ),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — never let a classifier failure touch the call
        logger.warning("voice_live_llm_check_failed", error=str(exc))
        return None
    return parse_judgement(result.content)


async def warm_up(api_key: str | None) -> None:
    """Make one throwaway classification at call start so the first real check
    doesn't pay the cold-connection cost (~2s), which would blow its timeout."""
    try:
        await judge_turn("ok", api_key, timeout=8.0)
    except Exception:  # noqa: BLE001 — purely an optimisation
        pass


# ---------------------------------------------------------------------------
# Live transcription connection
# ---------------------------------------------------------------------------

DeltaCallback = Callable[[str], Awaitable[None]]


class LiveTranscriber:
    """A second OpenAI Realtime connection that streams partial transcripts of
    the caller's audio. One per call; ``feed`` the same base64 mu-law payloads
    that go to the main session, receive text through ``on_delta``.

    Never raises into the call: connection failures leave ``healthy`` False,
    and audio is queued (bounded) so a slow socket can't stall the call's own
    audio pump.
    """

    def __init__(self, api_key: str | None, on_delta: DeltaCallback, log: Any) -> None:
        self._api_key = api_key
        self._on_delta = on_delta
        self._log = log
        self._ws: Any = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=400)
        self._tasks: list[asyncio.Task[None]] = []
        self.healthy = False

    async def start(self) -> bool:
        if not self._api_key:
            return False
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    _REALTIME_TRANSCRIBE_URL,
                    additional_headers={"Authorization": f"Bearer {self._api_key}"},
                    max_size=None,
                ),
                timeout=5,
            )
            await self._ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "transcription",
                            "audio": {
                                "input": {
                                    # Same format the phone provider sends — no resampling.
                                    "format": {"type": "audio/pcmu"},
                                    # language="hi" is a bias hint, not a hard
                                    # constraint — see realtime_bridge.py's main
                                    # transcription config for why: without it,
                                    # short/ambiguous Hindi speech was getting
                                    # hallucinated as text in unrelated languages.
                                    "transcription": {
                                        "model": settings.voice_live_transcribe_model,
                                        "delay": settings.voice_live_transcribe_delay,
                                        "language": "hi",
                                    },
                                    # This model does its own segmentation; passing
                                    # server VAD settings is rejected.
                                    "turn_detection": None,
                                }
                            },
                        },
                    }
                )
            )
            # Wait for the session to acknowledge (or reject) the config.
            deadline = time.monotonic() + 5
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                event = json.loads(raw)
                if event.get("type") == "session.updated":
                    break
                if event.get("type") == "error":
                    self._log.warning("voice_live_transcriber_rejected", error=event.get("error"))
                    await self.close()
                    return False
        except Exception as exc:  # noqa: BLE001
            self._log.warning("voice_live_transcriber_start_failed", error=str(exc))
            await self.close()
            return False

        self.healthy = True
        self._tasks = [
            asyncio.create_task(self._send_loop()),
            asyncio.create_task(self._read_loop()),
        ]
        self._log.info("voice_live_transcriber_ready")
        return True

    def feed(self, payload_b64: str) -> None:
        """Queue caller audio (base64 mu-law). Non-blocking; drops on overflow."""
        if not self.healthy:
            return
        try:
            self._queue.put_nowait(payload_b64)
        except asyncio.QueueFull:
            pass

    async def _send_loop(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                await self._ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": payload}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.warning("voice_live_transcriber_send_failed", error=str(exc))
            self.healthy = False

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                etype = event.get("type")
                if etype == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta") or ""
                    if delta:
                        try:
                            await self._on_delta(delta)
                        except Exception as exc:  # noqa: BLE001
                            self._log.warning("voice_live_delta_handler_failed", error=str(exc))
                elif etype == "error":
                    self._log.warning("voice_live_transcriber_error", error=event.get("error"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.info("voice_live_transcriber_closed", error=str(exc))
        finally:
            self.healthy = False

    async def close(self) -> None:
        self.healthy = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
