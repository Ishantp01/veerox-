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

from apps.api.core.conversation.config import EngineConfig
from apps.api.core.conversation.types import (
    ALLOWED_ACTIONS,
    EMOTIONS,
    LANGUAGES,
    MEMORY_KEYS,
    SCENARIOS,
    STATE_AWAITING_APPOINTMENT_TIME,
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
# Common Hindi words as typed in Latin script, including the spelling variants
# people actually use ("bje", "krna", "nhi", "shaam"/"sham"). Words that are
# also ordinary English words (to, me, ho, hi, par, pe, main, is) are left out
# on purpose: they would turn English text into Hinglish.
_HINGLISH_WORDS = re.compile(
    r"\b(hai|hain|hay|nahi|nahin|nhi|nai|kya|kyaa|kyun|kyu|kaise|kaisa|kaun|kitna|kitne|kitni|"
    r"kab|kal|aaj|abhi|parso|mujhe|mujhko|mera|meri|mere|tumhara|aapka|apka|aap|hum|humko|"
    r"bhai|bhaiya|ji|haan|han|theek|thik|chahiye|chahie|chaiye|karo|kro|karna|krna|karni|krni|"
    r"karunga|karungi|kijiye|kijiyega|batao|btao|bataiye|btaiye|bataye|bhejo|bhej|dijiye|do|"
    r"paisa|paise|rupay|rupaye|baad|baje|bje|bajey|mat|zyada|jyada|bahut|bohot|bhot|samajh|samjha|"
    r"samjhao|wala|wali|vala|vali|se|ko|ka|ki|ke|shaam|sham|subah|subha|dopahar|raat|din|"
    r"lekin|magar|aur|bhi|sirf|kuch|sab|toh|hoon|hun|hu|raha|rahi|rahe|chal|milega|milegi|"
    r"lena|dena|lunga|dunga|pata|accha|achha|acha|bilkul|zaroor|jaldi|dobara|phir|fir|"
    r"hoga|hogi|hoge|honge|hota|hoti|hona|hua|hui)\b",
    re.IGNORECASE,
)

# Hindi words that are also plain English words: they only count when at least
# one unambiguous Hindi word is present too.
_AMBIGUOUS_HINDI = frozenset({"do", "hu", "din", "sab", "toh", "chal", "fir", "pata", "lena", "dena", "ji"})


def detect_language(text: str) -> str:
    """Cheap script/word-list language guess: hi (Devanagari), hinglish
    (Latin script with Hindi words), else en."""
    if _DEVANAGARI.search(text):
        return "hi"
    # Collapse stretched spellings ("haiii", "kyaaa") before matching.
    normalized = re.sub(r"(.)\1{2,}", r"\1", text)
    hits = [w.lower() for w in _HINGLISH_WORDS.findall(normalized)]
    strong = [w for w in hits if w not in _AMBIGUOUS_HINDI]
    if len(hits) >= 2 and strong:
        return "hinglish"
    # A very short message with one distinctive Hindi word ("price kitna") is
    # still Hinglish; short function words (se, ko, ka, ...) don't count alone.
    if len(normalized.split()) <= 4 and any(len(w) >= 4 for w in strong):
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
                r"(not interested|no interest|don'?t (call|message|contact)|stop (calling|messaging|sending|texting|contacting|bothering)|"
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
                r"call karna|call krna|call kro|call karo|call kr do|phir se call|try again|vyast|व्यस्त|बाद में|कल फोन|कल कॉल)",
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
                r"cheaper|already (have|use|using)|competitor|not sure|think about it|"
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
    "appointment_request": [
        (
            re.compile(
                r"(?=.*(appointment|demo|meeting|slot|visit|डेमो|मीटिंग|अपॉइंटमेंट))"
                r"(?=.*(book|schedule|fix|arrange|set up|rakho|rakh do|karo|karna|krna|kro|chahiye|lena|"
                r"want|need|like|बुक|चाहिए))",
                re.IGNORECASE | re.DOTALL,
            ),
            0.85,
        ),
    ],
    "whatsapp_info_request": [
        (
            re.compile(
                r"(?=.*(whatsapp|whats app|व्हाट्सएप|व्हाट्सऐप))"
                r"(?=.*(send|share|bhej|bhejo|details|info|information|dijiye|de do|karo|forward|भेज))",
                re.IGNORECASE | re.DOTALL,
            ),
            0.85,  # an explicit "on WhatsApp" request beats the price/plan words in it
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
    # --- Scenario library additions (quotation 2.3) -------------------------
    "identification": [
        (
            re.compile(
                r"(yes[, ]+speaking|this is (me|he|she)\b|haan (ji )?(bol raha|bol rahi|boliye|bolo)|"
                r"ji bol raha|bol raha (hoon|hun|hu)\b|bol rahi (hoon|hun|hu)\b|ji boliye|"
                r"हाँ बोल रहा|जी बोलिए)",
                re.IGNORECASE,
            ),
            0.78,
        ),
    ],
    "discovery": [
        (
            re.compile(
                r"(i (run|own|manage|have) (a |an |the |my )?[a-z ]{0,25}"
                r"(business|agency|shop|store|company|firm|clinic|restaurant|hotel|school|salon|showroom|startup|factory)|"
                r"we (run|own|manage|are a|have a) [a-z ]{0,25}"
                r"(business|agency|shop|store|company|firm|clinic|restaurant|hotel|school|salon|showroom|startup|factory)|"
                r"my (business|company|shop|store|agency|firm|clinic)|"
                r"our (business|company|shop|store|agency|firm|problem|challenge)|"
                r"(mera|hamara|meri|hamari) [a-z ]{0,25}(business|dukaan|shop|agency|company|clinic|hotel|school|team)|"
                r"(problem|issue|challenge|dikkat|pareshani) (is|hai|yeh hai)|team size|employees|"
                r"मेरा .{0,20}(बिज़नेस|दुकान|कंपनी)|हमारा .{0,20}(बिज़नेस|दुकान|कंपनी))",
                re.IGNORECASE,
            ),
            0.75,
        ),
    ],
    "discount_request": [
        (
            re.compile(
                r"(discount|any offers?|koi offer|offers? hai|best price|final price|last price|"
                r"reduce (the )?price|price (kam|kum)|kam kar(o|na|do)|rate (kam|kum)|negotiat|"
                r"thoda (kam|sasta)|sasta|छूट|डिस्काउंट|ऑफर)",
                re.IGNORECASE,
            ),
            0.88,
        ),
    ],
    "closing": [
        (
            re.compile(
                r"^\s*(?:(?:ok(?:ay)?|theek hai|thik hai|accha|achha|ji|alright|fine|great|sure|cool)[\s,.!]+)*"
                r"(?:thanks?(?: you)?|thank you|dhanyavaad|dhanyawad|shukriya|bye|goodbye|good ?bye|"
                r"that'?s all|that is all|nothing else|bas(?: itna(?: hi)?)?|धन्यवाद|शुक्रिया)"
                r"(?:[\s,.!]+\w+){0,3}[\s.!]*$",
                re.IGNORECASE,
            ),
            0.8,
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
    if source.startswith("(?=") or not (source.startswith("(") and source.endswith(")")):
        return pattern  # lookahead rules match anywhere; anchored ones stay as written
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
    "appointment_request": "positive",
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


_BUSINESS_NOUNS = (
    r"business|agency|shop|store|company|firm|clinic|restaurant|hotel|school|salon|showroom|"
    r"startup|factory|dukaan"
)
_BUSINESS_RE = re.compile(
    rf"(?:\b(?:i|we)\s+(?:run|own|manage|have)\s+(?:a|an|the|my|our)?\s*|"
    rf"\b(?:mera|hamara|meri|hamari|my|our)\s+)((?:[a-z]+\s+){{0,2}}(?:{_BUSINESS_NOUNS}))\b",
    re.IGNORECASE,
)
_PAIN_RE = re.compile(
    r"(?<!no )(?<!koi )\b(?:problem|issue|challenge|struggling with|difficulty|dikkat|pareshani|takleef)"
    r"\s+(?:is|hai|yeh hai|ye hai|with|in|ki|that)?\s*([^.!?\n]{4,120})",
    re.IGNORECASE,
)
_PAIN_REJECT = re.compile(r"^(nahi|nhi|nahin|no\b|not\b|koi nahi)", re.IGNORECASE)


def _extract_facts(text: str, today: date) -> dict[str, str]:
    facts: dict[str, str] = {}
    name_match = _NAME_RE.search(text)
    if name_match and name_match.group(1).lower() not in _NAME_STOPWORDS:
        facts["name"] = name_match.group(1).strip().title()

    business_match = _BUSINESS_RE.search(text)
    if business_match:
        facts["business_type"] = business_match.group(1).strip().title()[:80]
    pain_match = _PAIN_RE.search(text)
    if pain_match:
        pain = pain_match.group(1).strip(" ,;:-")
        if pain and not _PAIN_REJECT.match(pain):
            facts["pain_point"] = pain[:120]

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


_BUSY_CUE_RE = re.compile(
    r"(busy|later\b|meeting|driving|not a good time|abhi (time )?nahi|vyast|व्यस्त|बाद में|baad (me|mein))",
    re.IGNORECASE,
)
# Weight of an admin-written example phrase: above the generic rules so a
# business's own wording wins, below the hard-stop scenarios.
_EXAMPLE_WEIGHT = 0.87


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)).strip()


def _match_examples(text: str, config: EngineConfig) -> list[tuple[str, float]]:
    """Scenarios whose admin-written example phrases appear in the text."""
    normalised = f" {_normalise(text)} "
    found: list[tuple[str, float]] = []
    for scenario in config.scenarios.values():
        if not scenario.enabled:
            continue
        for example in scenario.examples:
            needle = _normalise(example)
            if needle and f" {needle} " in normalised:
                found.append((scenario.id, _EXAMPLE_WEIGHT))
                break
    return found


def classify_with_rules(
    text: str,
    *,
    state: str | None,
    today: date | None = None,
    config: EngineConfig | None = None,
) -> Understanding | None:
    """Return a confident Understanding, or None when the LLM should decide."""
    today = today or date.today()
    language = detect_language(text)
    facts = _extract_facts(text, today)

    matched: list[tuple[str, float]] = []
    for scenario, patterns in _RULES.items():
        if config is not None and not config.is_enabled(scenario):
            continue  # switched off in the Scenario Builder
        for pattern, weight in patterns:
            if pattern.search(text):
                matched.append((scenario, weight))
                break
    if config is not None:
        for scenario_id, weight in _match_examples(text, config):
            matched = [m for m in matched if m[0] != scenario_id]
            matched.append((scenario_id, weight))

    # We asked "when should we call?" last turn and the customer answered with
    # a time — that is a callback request even if no keyword matched.
    # Only when the message is otherwise unremarkable: a time in a sentence that
    # clearly asks for something else (a demo, a price, details on WhatsApp)
    # belongs to that request, not to the callback we were waiting on.
    if state == STATE_AWAITING_CALLBACK_TIME and "callback_time_hhmm" in facts:
        if all(s in ("busy_callback", "interested", "greeting") for s, _ in matched):
            return Understanding(
                scenario="busy_callback",
                intent="callback_time_given",
                emotion="neutral",
                language=language,
                confidence=0.9,
                source="rules",
                facts=facts,
            )

    # Same for an appointment: we asked "which day and time?" and got a time.
    if state == STATE_AWAITING_APPOINTMENT_TIME and "callback_time_hhmm" in facts:
        if all(s in ("appointment_request", "interested", "greeting") for s, _ in matched):
            return Understanding(
                scenario="appointment_request",
                intent="appointment_time_given",
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
    elif len(matched) > 1 and round(top_weight - matched[1][1], 2) < _CLEAR_MARGIN:
        # price_question + objection etc. — let the LLM arbitrate.
        return None
    else:
        confidence = top_weight

    # A time stated with a "busy"/"later" cue is a callback, not a price chat.
    if top_scenario == "busy_callback" and "callback_time_hhmm" in facts:
        confidence = min(_MAX_CONFIDENCE, confidence + 0.05)

    # "Call me at 6" without any sign the customer is busy is a plain callback
    # request, not a customer-busy situation; both are handled alike.
    if (
        top_scenario == "busy_callback"
        and not _BUSY_CUE_RE.search(text)
        and config is not None
        and config.is_enabled("callback_request")
    ):
        top_scenario = "callback_request"

    return Understanding(
        scenario=top_scenario,
        intent=top_scenario,
        emotion=_EMOTION_BY_SCENARIO.get(top_scenario, "neutral"),
        language=language,
        confidence=min(confidence, _MAX_CONFIDENCE),
        source="rules",
        facts=facts,
    )


def _llm_system(config: EngineConfig | None) -> str:
    ids = config.enabled_ids() if config is not None else list(SCENARIOS)
    catalog = ""
    if config is not None:
        lines = []
        for scenario_id in ids:
            scenario = config.get(scenario_id)
            if scenario is None:
                continue
            line = f"- {scenario.id}: {scenario.name}"
            if scenario.situation:
                line += f" ({scenario.situation})"
            if scenario.examples:
                line += " e.g. " + "; ".join(f'"{e}"' for e in scenario.examples[:4])
            lines.append(line[:400])
        catalog = "\nScenarios:\n" + "\n".join(lines) + "\n"
    return (
        "You classify ONE customer message for a sales/support assistant. Reply with a single JSON "
        "object and nothing else, with keys: "
        f'"scenario" (one of {ids + ["other"]}), '
        '"intent" (short snake_case label), '
        f'"emotion" (one of {list(EMOTIONS)}), '
        f'"language" (one of {list(LANGUAGES)}), '
        '"confidence" (number 0..1), '
        '"facts" (object, optional keys name, business_type, pain_point, preferred_channel, '
        "callback_date as YYYY-MM-DD, callback_time_hhmm as HH:MM 24h), "
        f'"action_id" (one of {sorted(ALLOWED_ACTIONS)}). '
        'Use "unknown_question" when the customer asks something factual about the business. '
        'Use "other" only for messages that fit no scenario. Never invent facts the customer did not state.'
        f"{catalog}"
    )


_LLM_SYSTEM = _llm_system(None)


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
    config: EngineConfig | None = None,
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
        {"role": "system", "content": _llm_system(config) if config is not None else _LLM_SYSTEM},
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
    valid = set(config.ids()) if config is not None else set(SCENARIOS)
    if scenario not in valid:
        raise UnderstandError("invalid_output")
    if config is not None and not config.is_enabled(scenario):
        raise UnderstandError("scenario_disabled")
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
    config: EngineConfig | None = None,
) -> Understanding:
    ruled = classify_with_rules(text, state=state, today=today, config=config)
    if ruled is not None:
        return ruled
    return await classify_with_llm(
        text,
        history=history,
        state=state,
        memory=memory,
        llm=llm,
        timeout=timeout,
        today=today,
        config=config,
    )
