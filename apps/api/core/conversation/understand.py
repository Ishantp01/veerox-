"""Process 2.0 — understand a customer turn.

Rules decide the clear cases (no LLM cost, no latency). Only when the rules
are unsure — nothing matched, or two scenarios matched with similar weight —
does one LLM decision call run. Any LLM trouble raises ``UnderstandError``
and the engine falls back to the legacy script path.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import structlog

from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    EMOTIONS,
    LANGUAGES,
    MEMORY_KEYS,
    SCENARIOS,
    STATE_AWAITING_CALLBACK_TIME,
    Understanding,
)
from apps.api.core.llm import ChatResult

logger = structlog.get_logger(__name__)

# (messages, timeout_seconds) -> ChatResult. Injected so tests never hit the
# network and so the engine owns the timeout.
LLMCall = Callable[[list[dict[str, Any]]], Awaitable[ChatResult]]


class UnderstandError(Exception):
    """The LLM decision call timed out or returned unusable output."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH_WORDS = re.compile(
    r"\b(hai|hain|nahi|nahin|nhi|kya|kitna|kitne|kitni|kab|kal|abhi|mujhe|mera|meri|"
    r"aap|apka|aapka|bhai|ji|haan|han|theek|thik|chahiye|karo|kijiye|batao|bataiye|"
    r"paisa|rupay|rupaye|baad|baje|mat|zyada|jyada|bahut|samajh|samjha|wala|wali|se|ko|ka|ki|ke)\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """Cheap script/word-list language guess: hi (Devanagari), hinglish
    (Latin script with Hindi words), else en."""
    if _DEVANAGARI.search(text):
        return "hi"
    if len(_HINGLISH_WORDS.findall(text)) >= 2:
        return "hinglish"
    return "en"


# Rule table: scenario -> (regex, weight). Higher weight = stronger signal.
# Patterns cover English, Hinglish (Latin) and Devanagari Hindi.
_RULES: dict[str, list[tuple[re.Pattern[str], float]]] = {
    "human_handoff": [
        (
            re.compile(
                r"(human\b|real person|live agent|agent se|insaan|manager|supervisor|"
                r"someone from (your )?team|talk to (a |an )?(person|agent|executive)|"
                r"connect me|executive se|kisi (se )?baat|इंसान|एजेंट|मैनेजर|किसी से बात)",
                re.IGNORECASE,
            ),
            0.9,
        ),
    ],
    "angry": [
        (
            re.compile(
                r"(stupid|useless|waste of|worst\b|pathetic|fraud|cheat|scam\b|harass|"
                r"sick of|fed up|bakwas|bekar|bekaar|pagal|tang (aa|kar)|gussa|"
                r"complain|ullu|बकवास|बेकार|धोखा|परेशान|शिकायत)",
                re.IGNORECASE,
            ),
            0.9,
        ),
    ],
    "not_interested": [
        (
            re.compile(
                r"(not interested|no interest|don'?t (call|message|contact)|stop (calling|messaging)|"
                r"remove (me|my number)|unsubscribe|nahi chahiye|nahin chahiye|nhi chahiye|"
                r"interested nahi|interest nahi|mat karo|mat karna|dobara mat|"
                r"रुचि नहीं|नहीं चाहिए|मत करो|इंटरेस्टेड नहीं)",
                re.IGNORECASE,
            ),
            0.9,
        ),
    ],
    "busy_callback": [
        (
            re.compile(
                r"(busy|call (me )?(back|later|tomorrow|after)|call kal|kal call|baad (me|mein) call|"
                r"later\b|abhi (time )?nahi|abhi busy|meeting (me|mein)|driving|not a good time|"
                r"call karna|phir se call|try again|vyast|व्यस्त|बाद में|कल फोन|कल कॉल)",
                re.IGNORECASE,
            ),
            0.85,
        ),
    ],
    "price_question": [
        (
            re.compile(
                r"(price|pricing|costs?\b|charges?\b|fees?\b|how much|rates?\b|kitna|kitne ka|kitni|"
                r"paisa|paise|rupay|rupees?|rs\.?\s?\d|₹|plans?\b|package|kimat|keemat|कीमत|कितना|कितने|दाम|चार्ज)",
                re.IGNORECASE,
            ),
            0.8,
        ),
    ],
    "objection": [
        (
            re.compile(
                r"(too (expensive|costly|much)|expensive|costly|mehenga|mehnga|bahut zyada|"
                r"discount|cheaper|already (have|use|using)|competitor|not sure|think about it|"
                r"sochna|sochta|sochungi|sochunga|budget (nahi|nhi|issue)|महंगा|सोचना)",
                re.IGNORECASE,
            ),
            0.85,
        ),
    ],
    "interested": [
        (
            re.compile(
                r"(interested|sounds good|tell me more|send (me )?(details|info)|i want|i'?d like|"
                r"sign me up|let'?s do|go ahead|yes please|haan (chahiye|batao|bhejo)|"
                r"details (bhejo|do|send)|batao|bataiye|bhej do|ready\b|chahiye|इंटरेस्टेड|बताइए|भेज दो|चाहिए)",
                re.IGNORECASE,
            ),
            0.75,
        ),
    ],
    "confused": [
        (
            re.compile(
                r"(confus|don'?t understand|didn'?t understand|what do you mean|what\?|"
                r"samajh (nahi|nhi)|samjha nahi|matlab kya|kya matlab|kaun (ho|hai|bol)|who (is|are) (this|you)|"
                r"kya bol rahe|समझ नहीं|मतलब क्या|कौन)",
                re.IGNORECASE,
            ),
            0.8,
        ),
    ],
    "greeting": [
        (
            re.compile(
                r"^\s*(hi+|hello+|hey+|hola|namaste|namaskar|good (morning|afternoon|evening)|"
                r"hii+|helo|नमस्ते|हेलो|हाय)\W*$",
                re.IGNORECASE,
            ),
            0.85,
        ),
    ],
}

def _split_top_level(pattern: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    escaped = False
    for ch in pattern:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _guard_alternatives(pattern: re.Pattern[str]) -> re.Pattern[str]:
    """Require every alternative to start at a word start, not mid-word,
    otherwise "rate" matches inside "integrate" and "ready" inside
    "already". Latin-letter lookbehind only, so Devanagari alternatives are
    unaffected. Anchored patterns (greeting) are left alone."""
    source = pattern.pattern
    if not (source.startswith("(") and source.endswith(")")):
        return pattern
    alternatives = _split_top_level(source[1:-1])
    guarded = "|".join(f"(?<![A-Za-z]){alt}" for alt in alternatives)
    return re.compile(f"({guarded})", pattern.flags)


_RULES = {
    scenario: [(_guard_alternatives(pattern), weight) for pattern, weight in patterns]
    for scenario, patterns in _RULES.items()
}

_EMOTION_BY_SCENARIO = {
    "angry": "angry",
    "not_interested": "negative",
    "objection": "negative",
    "confused": "confused",
    "interested": "positive",
}

# A rules result is "clear" when it beats every other matched scenario by at
# least this much weight; otherwise the LLM arbitrates.
_CLEAR_MARGIN = 0.05
_MAX_CONFIDENCE = 0.95

_TIME_RE = re.compile(
    r"(?:(?:at|after|around|by|ko|tak)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|बजे)?", re.IGNORECASE
)
_NAME_RE = re.compile(
    r"(?:my name is|i am|i'm|this is|mera naam|मेरा नाम|naam hai|main)\s+([A-Za-zऀ-ॿ]{2,20})",
    re.IGNORECASE,
)
_NAME_STOPWORDS = {
    "busy", "interested", "not", "fine", "ok", "okay", "here", "calling", "sure", "ready",
    "also", "from", "the", "a", "an", "hi", "hello", "very", "just", "so", "in", "at",
    "abhi", "ek", "aapka", "yahan", "bahut", "kal", "driving", "free", "currently",
}
# A preference is only recorded for an explicit channel mention. A bare "call"
# is NOT one: "call me tomorrow" or "stop calling me" say nothing about the
# channel the customer prefers.
_CHANNEL_RE = re.compile(
    r"(whatsapp|whats app|व्हाट्सएप|व्हाट्सऐप|\bsms\b|\bemail\b|"
    r"(?:prefer|pe|par|on)\s+(?:a\s+)?(?:phone\s+)?call\b)",
    re.IGNORECASE,
)


def _extract_facts(text: str, today: date) -> dict[str, str]:
    facts: dict[str, str] = {}
    name_match = _NAME_RE.search(text)
    if name_match and name_match.group(1).lower() not in _NAME_STOPWORDS:
        facts["name"] = name_match.group(1).strip().title()

    lowered = text.lower()
    callback_date: date | None = None
    if re.search(r"\b(tomorrow|kal)\b|कल", lowered):
        callback_date = today + timedelta(days=1)
    elif re.search(r"\b(today|aaj)\b|आज", lowered):
        callback_date = today
    elif re.search(r"\b(day after tomorrow|parso)\b|परसों", lowered):
        callback_date = today + timedelta(days=2)

    time_hhmm: str | None = None
    for match in _TIME_RE.finditer(text):
        hour_raw, minute_raw, suffix = match.group(1), match.group(2), (match.group(3) or "").lower()
        if not suffix and not minute_raw:
            continue  # a bare number is not a time ("2 products")
        hour = int(hour_raw)
        minute = int(minute_raw or 0)
        if suffix == "pm" and hour < 12:
            hour += 12
        elif suffix == "am" and hour == 12:
            hour = 0
        elif suffix in ("baje", "बजे") and 1 <= hour <= 6:
            hour += 12  # "5 baje" in a business context means evening
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            time_hhmm = f"{hour:02d}:{minute:02d}"
            break
    if time_hhmm is None:
        if re.search(r"\bmorning\b|subah|सुबह", lowered):
            time_hhmm = "10:00"
        elif re.search(r"\bafternoon\b|dopahar|दोपहर", lowered):
            time_hhmm = "14:00"
        elif re.search(r"\bevening\b|shaam|शाम", lowered):
            time_hhmm = "17:00"

    if callback_date is not None or time_hhmm is not None:
        facts["callback_date"] = (callback_date or today).isoformat()
        if time_hhmm is not None:
            facts["callback_time_hhmm"] = time_hhmm
            facts["callback_time"] = f"{facts['callback_date']} {time_hhmm}"

    channel_match = _CHANNEL_RE.search(text)
    if channel_match:
        word = channel_match.group(1).lower()
        if "whats" in word or "व्हाट" in word:
            facts["preferred_channel"] = "whatsapp"
        elif "email" in word:
            facts["preferred_channel"] = "email"
        elif "sms" in word:
            facts["preferred_channel"] = "sms"
        else:
            facts["preferred_channel"] = "call"
    return facts


def classify_with_rules(
    text: str, *, state: str | None, today: date | None = None
) -> Understanding | None:
    """Return a confident Understanding, or None when the LLM should decide."""
    today = today or date.today()
    language = detect_language(text)
    facts = _extract_facts(text, today)

    matched: list[tuple[str, float]] = []
    for scenario, patterns in _RULES.items():
        for pattern, weight in patterns:
            if pattern.search(text):
                matched.append((scenario, weight))
                break

    # We asked "when should we call?" last turn and the customer answered with
    # a time — that is a callback request even if no keyword matched.
    if state == STATE_AWAITING_CALLBACK_TIME and "callback_time_hhmm" in facts:
        if not any(s in ("not_interested", "angry", "human_handoff") for s, _ in matched):
            return Understanding(
                scenario="busy_callback",
                intent="callback_time_given",
                emotion="neutral",
                language=language,
                confidence=0.9,
                source="rules",
                facts=facts,
            )

    if not matched:
        return None

    matched.sort(key=lambda item: item[1], reverse=True)
    top_scenario, top_weight = matched[0]
    # Hard-stop scenarios always win outright when present.
    if top_scenario in ("human_handoff", "angry", "not_interested"):
        confidence = top_weight
    elif len(matched) > 1 and top_weight - matched[1][1] < _CLEAR_MARGIN:
        # price_question + objection etc. — let the LLM arbitrate.
        return None
    else:
        confidence = top_weight

    # A time stated with a "busy"/"later" cue is a callback, not a price chat.
    if top_scenario == "busy_callback" and "callback_time_hhmm" in facts:
        confidence = min(_MAX_CONFIDENCE, confidence + 0.05)

    return Understanding(
        scenario=top_scenario,
        intent=top_scenario,
        emotion=_EMOTION_BY_SCENARIO.get(top_scenario, "neutral"),
        language=language,
        confidence=min(confidence, _MAX_CONFIDENCE),
        source="rules",
        facts=facts,
    )


_LLM_SYSTEM = (
    "You classify ONE customer message for a sales/support assistant. Reply with a single JSON "
    "object and nothing else, with keys: "
    f'"scenario" (one of {list(SCENARIOS) + ["other"]}), '
    '"intent" (short snake_case label), '
    f'"emotion" (one of {list(EMOTIONS)}), '
    f'"language" (one of {list(LANGUAGES)}), '
    '"confidence" (number 0..1), '
    '"facts" (object, optional keys name, business_type, preferred_channel, '
    "callback_date as YYYY-MM-DD, callback_time_hhmm as HH:MM 24h), "
    f'"action_id" (one of {sorted(ALLOWED_ACTIONS)}). '
    'Use "unknown_question" when the customer asks something factual about the business. '
    'Use "other" only for messages that fit no scenario. Never invent facts the customer did not state.'
)


def _parse_llm_json(content: str | None) -> dict[str, Any]:
    if not content:
        raise UnderstandError("invalid_output")
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise UnderstandError("invalid_output") from None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise UnderstandError("invalid_output") from None
    if not isinstance(data, dict):
        raise UnderstandError("invalid_output")
    return data


async def classify_with_llm(
    text: str,
    *,
    history: list[dict[str, Any]],
    state: str | None,
    memory: dict[str, str],
    llm: LLMCall,
    timeout: float,
    today: date | None = None,
) -> Understanding:
    today = today or date.today()
    context_lines = [
        f"{m.get('role')}: {str(m.get('content') or '')[:300]}"
        for m in history[-4:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    user_block = (
        f"Today: {today.isoformat()}\n"
        f"Conversation state: {state or 'new'}\n"
        f"Known facts: {json.dumps(memory, ensure_ascii=False)}\n"
        f"Recent messages:\n" + ("\n".join(context_lines) or "(none)") + "\n\n"
        f"Customer message: {text}"
    )
    messages = [
        {"role": "system", "content": _LLM_SYSTEM},
        {"role": "user", "content": user_block},
    ]
    try:
        result = await asyncio.wait_for(llm(messages), timeout=timeout)
    except TimeoutError:
        raise UnderstandError("timeout") from None
    except Exception:  # noqa: BLE001 — any provider failure => legacy path
        logger.warning("conversation_llm_failed", exc_info=True)
        raise UnderstandError("llm_error") from None

    data = _parse_llm_json(result.content)
    scenario = str(data.get("scenario") or "")
    if scenario == "other":
        scenario = "unknown_question"
        confidence_cap = 0.5  # "fits nothing" must not look confident
    else:
        confidence_cap = 1.0
    if scenario not in SCENARIOS:
        raise UnderstandError("invalid_output")
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        raise UnderstandError("invalid_output") from None
    confidence = max(0.0, min(confidence, confidence_cap, 1.0))

    emotion = data.get("emotion") if data.get("emotion") in EMOTIONS else "neutral"
    # The reply language follows what the customer just wrote, not the model's
    # guess (which drifts toward the language of earlier turns in the history).
    language = detect_language(text)

    facts: dict[str, str] = {}
    raw_facts = data.get("facts")
    if isinstance(raw_facts, dict):
        for key in (*MEMORY_KEYS, "callback_date", "callback_time_hhmm"):
            value = raw_facts.get(key)
            if isinstance(value, str) and value.strip():
                facts[key] = value.strip()[:200]
    # Rules-extracted facts are more reliable for dates/times than the LLM's.
    facts = {**facts, **_extract_facts(text, today)}

    action_id = data.get("action_id")
    return Understanding(
        scenario=scenario,
        intent=str(data.get("intent") or scenario)[:32],
        emotion=emotion,
        language=language,
        confidence=confidence,
        source="llm",
        facts=facts,
        proposed_action=action_id if isinstance(action_id, str) else None,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )


async def understand(
    text: str,
    *,
    state: str | None,
    history: list[dict[str, Any]],
    memory: dict[str, str],
    llm: LLMCall,
    timeout: float,
    today: date | None = None,
) -> Understanding:
    ruled = classify_with_rules(text, state=state, today=today)
    if ruled is not None:
        return ruled
    return await classify_with_llm(
        text, history=history, state=state, memory=memory, llm=llm, timeout=timeout, today=today
    )
