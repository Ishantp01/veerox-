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
import contextvars
import json
import re
import time
from dataclasses import dataclass, field, replace
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

# Words that signal the caller is correcting themselves or the agent. Only consulted
# with VOICE_SMART_TURN_TAKING: "nahi"/"no" are already in QUESTION_CUES.
CORRECTION_CUES = frozenset(
    {
        "galat", "galti", "wrong", "incorrect", "sorry", "maaf", "meant",
        "correction", "dobara", "repeat",
        "गलत", "गलती", "माफ", "माफ़", "सॉरी", "दोबारा",
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


# Hums and breaths the transcriber spells with a variable number of letters ("hmmmm", "uhhh", "aaah").
_HUM_RE = re.compile(r"^(?:h?m{2,}|u+h*m+|u+h+|a+h+|o+h+|e+h+)$")


def _is_backchannel_word(word: str) -> bool:
    return word in BACKCHANNEL_WORDS or bool(_HUM_RE.match(word))


def is_backchannel(text: str, max_words: int = 4) -> bool:
    """True for empty/noise transcripts and short filler acknowledgments
    ("ok", "theek hai", "accha", "haan ji") — not real questions."""
    words = _tokens(text)
    if not words:
        return True
    return len(words) <= max_words and all(_is_backchannel_word(w) for w in words)


def is_noise(text: str) -> bool:
    """Non-speech the transcriber emitted for background noise: only punctuation or
    symbols, or one letter repeated ("aaaa", "mmmm"). Empty text is NOT noise (it
    just means nothing has been transcribed yet)."""
    stripped = text.strip()
    if not stripped:
        return False
    words = _tokens(stripped)
    if not words:
        return True  # punctuation / symbols only
    return all(len(w) >= 3 and len(set(w)) == 1 and w.isalpha() for w in words)


def classify_partial(text: str, smart: bool = False) -> Verdict:
    """Cheap first-pass verdict on the (possibly partial, possibly garbled)
    transcript of what the caller is saying over the agent.

    "filler"  — only acknowledgment words: keep talking.
    "real"    — clearly a turn: a question/request cue, or a long utterance
                with real content: stop and answer.
    "unsure"  — anything else (other languages, garbled text): ask the LLM.

    A "?" is deliberately NOT treated as proof of a question — the
    transcriber sprinkles them on fillers in some languages.
    """
    if smart and is_noise(text):
        return "filler"  # background noise is not a turn: keep talking
    words = _tokens(text)
    if not words:
        return "unsure"
    if smart and any(w in CORRECTION_CUES for w in words):
        return "real"  # "galat", "sorry, I meant...": stop and listen
    non_filler = [w for w in words if not _is_backchannel_word(w)]
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


# VOICE_SMART_TURN_TAKING only. A caller who says they need a moment is not done: wait longer than for an
# ordinary mid-sentence pause. Only counts when it is how the utterance ENDS, so "ek minute... paanch lakh"
# stops being a thinking pause as soon as the number arrives.
HOLD_THINKING_SECONDS = 4.0
_THINKING_RE = re.compile(
    r"(?:\b(?:ek|1)\s*(?:minute|min|second|sec|moment|baar)|\bone\s+(?:minute|second|moment)|"
    r"\bjust\s+a\s+(?:minute|second|sec|moment)|\bhold\s+on|\bgive\s+me\s+a\s+(?:minute|second|moment)|"
    r"\blet\s+me\s+think|\bsoch\s+(?:raha|rahi|rha|rhi)\b|\bsochne\s+do|\bsochta\s+hoon|\bsochti\s+hoon|"
    r"\bruko\b|\bruk\s+jao\b|\brukiye\b|\bdekhta\s+hoon|\bdekhti\s+hoon|\bwait\b|"
    r"एक\s+(?:मिनट|सेकंड|सेकण्ड)|रुको|रुकिए|सोच\s+(?:रहा|रही))"
    r"[\s.,!…\-]*(?:ji|please|na|hai|hoon|toh|ok|okay|sir|madam)?[\s.,!…\-]*$",
    re.IGNORECASE,
)
# Words a sentence cannot end on: it names the thing but not what is being said about it.
OPEN_ENDERS = frozenset(
    {
        "ke", "ka", "ko", "se", "mein", "me", "par", "pe", "liye", "baare", "bare", "about", "for", "with", "in",
        "on", "at", "around", "roughly", "approximately", "shayad", "maybe", "perhaps", "kaafi", "bahut", "thoda",
        "को", "के", "का", "से", "में", "पर", "लिए", "बारे", "शायद",
    }
)
_POSSESSIVES = frozenset({"mera", "meri", "mere", "my", "our", "hamara", "hamari", "mujhe", "i", "main", "मेरा", "मेरी", "मेरे"})
_CONDITIONAL = frozenset({"agar", "if", "jab", "when", "अगर", "जब"})
_THEN = frozenset({"toh", "to", "then", "तो"})


def thinking_cue(text: str) -> bool:
    """The caller ended on "ek minute" / "let me think" / "ruko": they asked for time, not an answer."""
    return bool(_THINKING_RE.search(text.strip()))


def _unfinished(words: list[str]) -> bool:
    """Heuristics for a sentence that has begun but not said anything yet (smart mode only)."""
    if words[-1] in OPEN_ENDERS:
        return True
    if len(words) <= 3 and words[0] in _POSSESSIVES:
        return True  # "mera budget" - what about it?
    if any(w in _CONDITIONAL for w in words) and not any(w in _THEN for w in words[-3:]) and len(words) <= 12:
        # "agar main investment karun" - the "then" part is still to come. A question mark or a
        # question cue means they asked something complete.
        return not any(w in QUESTION_CUES for w in words)
    return False


def hold_decision(text: str, agent_was_busy: bool, smart: bool = False) -> tuple[float, str | None]:
    """How long to wait before answering, and why: ``(seconds, reason)``; ``(0.0, None)`` when the turn looks
    finished. Reasons: ``thinking_request``, ``lone_filler``, ``trailing_punctuation``, ``continuation_word``,
    ``unfinished_phrase``. All timings are the ``HOLD_*`` constants above - the one place to tune them.

    Turn end is otherwise decided by a fixed 0.3s silence, which cuts off a caller who pauses mid-sentence
    ("haan… mera matlab…"). Streamed text lags the audio by up to ~1s, so this looks at a *prefix*: callers
    re-check it as more text arrives and release the hold as soon as it stops looking unfinished (see
    adapter.handle_live_delta).

    ``smart`` (VOICE_SMART_TURN_TAKING) adds waiting longer after "ek minute"-style requests for time, and
    holding for sentences that stop on a postposition, a bare possessive or an open "agar ..." clause.
    """
    stripped = text.strip()
    words = _tokens(stripped)
    if not words:
        return 0.0, None  # no text yet — don't guess, answer normally
    if smart and thinking_cue(stripped):
        return HOLD_THINKING_SECONDS, "thinking_request"
    lone_filler = len(words) <= 2 and all(_is_backchannel_word(w) for w in words)
    if lone_filler and not agent_was_busy:
        # A bare "haan"/"ok" to an idle agent is very often followed by more.
        return HOLD_LONE_FILLER_SECONDS, "lone_filler"
    if stripped.endswith(_TERMINAL) and not stripped.endswith("..."):
        return 0.0, None
    if stripped.endswith(("…", "...", ",")):
        return HOLD_CONTINUATION_SECONDS, "trailing_punctuation"
    if words[-1] in CONTINUATION_WORDS:
        return HOLD_CONTINUATION_SECONDS, "continuation_word"
    if smart and _unfinished(words):
        return HOLD_CONTINUATION_SECONDS, "unfinished_phrase"
    return 0.0, None


def endpoint_hold_seconds(text: str, agent_was_busy: bool, smart: bool = False) -> float:
    """Seconds to keep waiting for the caller to continue; see ``hold_decision``."""
    return hold_decision(text, agent_was_busy, smart)[0]


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


CUT_REASONS = frozenset({"duration", "live_fallback", "live_real"} | {f"live_llm_{label.lower()}" for label in TURN_LABELS})
JUDGE_LABELS = frozenset(label.lower() for label in TURN_LABELS)


@dataclass
class LiveTurnStats:
    """Per-call counters for the live turn-detection path. Numbers only: no transcript text, no phone numbers.

    ``extra_provider_requests`` is what this feature adds on top of the call itself: the one warm-up and every judging
    request (chat completions), plus the transcription connection, which also streams the caller's audio all call long."""

    transcriber: str = "not_attempted"  # not_attempted | started | failed
    warmup_requests: int = 0
    warmup_failures: int = 0
    judge_requests: int = 0
    judge_timeouts: int = 0
    judge_errors: int = 0
    judge_unparsable: int = 0
    judge_late: int = 0  # answered, but after the window in which it could still change anything
    judge_labels: dict[str, int] = field(default_factory=dict)
    cuts: dict[str, int] = field(default_factory=dict)  # interruption decisions that stopped the agent, by reason
    filler_ignored: int = 0  # interruptions judged to be filler: the agent kept talking
    fallback_filler_kept: int = 0  # the safety-net timer fired but the words so far were filler

    def count(self, bucket: dict[str, int], key: str, allowed: frozenset[str]) -> None:
        """Count ``key`` only if it is one of the fixed ``allowed`` names; anything else is counted as "other", so no
        free text (a number, a key, a word the caller said) can ever become a log key."""
        safe = key if key in allowed else "other"
        bucket[safe] = bucket.get(safe, 0) + 1

    def count_cut(self, reason: str) -> None:
        self.count(self.cuts, reason, CUT_REASONS)

    def count_label(self, label: str) -> None:
        self.count(self.judge_labels, (label or "").lower(), JUDGE_LABELS)

    @property
    def extra_provider_requests(self) -> int:
        return self.warmup_requests + self.judge_requests + (1 if self.transcriber == "started" else 0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "transcriber": self.transcriber,
            "extra_provider_requests": self.extra_provider_requests,
            "warmup_requests": self.warmup_requests,
            "warmup_failures": self.warmup_failures,
            "judge_requests": self.judge_requests,
            "judge_timeouts": self.judge_timeouts,
            "judge_errors": self.judge_errors,
            "judge_unparsable": self.judge_unparsable,
            "judge_late": self.judge_late,
            "judge_labels": dict(self.judge_labels),
            "cuts": dict(self.cuts),
            "cuts_total": sum(self.cuts.values()),
            "filler_ignored": self.filler_ignored,
            "fallback_filler_kept": self.fallback_filler_kept,
        }


SUMMARY_KEYS = frozenset(
    {"transcriber", "extra_provider_requests", "warmup_requests", "warmup_failures", "judge_requests", "judge_timeouts", "judge_errors",
     "judge_unparsable", "judge_late", "judge_labels", "cuts", "cuts_total", "filler_ignored", "fallback_filler_kept",
     "judge_tokens_in", "judge_tokens_out"}
)


def summary_fields(stats: LiveTurnStats, judge_tokens_in: int, judge_tokens_out: int) -> dict[str, Any]:
    """The fields of the one end-of-call log line: counts and fixed labels only, from a closed set of keys."""
    fields = stats.snapshot()
    fields["judge_tokens_in"], fields["judge_tokens_out"] = int(judge_tokens_in or 0), int(judge_tokens_out or 0)
    return {k: v for k, v in fields.items() if k in SUMMARY_KEYS}


# The call whose judging request is in flight in this task, so ``judge_turn`` can count it without a new parameter
# (callers and test doubles keep the original signature).
ACTIVE_STATS: contextvars.ContextVar[LiveTurnStats | None] = contextvars.ContextVar("live_turn_stats", default=None)


@dataclass(frozen=True)
class TurnJudgement:
    label: str
    confidence: float
    # What the judging call cost (for usage metering); not part of the verdict's identity.
    tokens_in: int = field(default=0, compare=False)
    tokens_out: int = field(default=0, compare=False)

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


async def judge_turn(
    text: str, api_key: str | None, timeout: float = 0.8, *, stats: LiveTurnStats | None = None, warmup: bool = False
) -> TurnJudgement | None:
    """Ask a small, fast model to label ``text`` (any language) with a
    confidence, or None if it couldn't answer in time."""
    from apps.api.core.llm import chat_completion  # local: keeps import cost off startup

    stats = stats or ACTIVE_STATS.get()
    if stats is not None:
        if warmup:
            stats.warmup_requests += 1
        else:
            stats.judge_requests += 1
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
        logger.warning("voice_live_llm_check_failed", error_type=type(exc).__name__)
        if stats is not None:
            if warmup:
                stats.warmup_failures += 1
            elif isinstance(exc, asyncio.TimeoutError):
                stats.judge_timeouts += 1
            else:
                stats.judge_errors += 1
        return None
    judged = parse_judgement(result.content)
    if judged is None:
        if stats is not None and not warmup:
            stats.judge_unparsable += 1
        return None
    return replace(judged, tokens_in=int(result.tokens_in or 0), tokens_out=int(result.tokens_out or 0))


async def warm_up(api_key: str | None, stats: LiveTurnStats | None = None) -> None:
    """Make one throwaway classification at call start so the first real check
    doesn't pay the cold-connection cost (~2s), which would blow its timeout."""
    try:
        await judge_turn("ok", api_key, timeout=8.0, stats=stats, warmup=True)
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
                    self._log.warning("voice_live_transcriber_rejected", provider_error=True)
                    await self.close()
                    return False
        except Exception as exc:  # noqa: BLE001
            self._log.warning("voice_live_transcriber_start_failed", error_type=type(exc).__name__)
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
            self._log.warning("voice_live_transcriber_send_failed", error_type=type(exc).__name__)
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
                            self._log.warning("voice_live_delta_handler_failed", error_type=type(exc).__name__)
                elif etype == "error":
                    self._log.warning("voice_live_transcriber_error", provider_error=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._log.info("voice_live_transcriber_closed", error_type=type(exc).__name__)
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
