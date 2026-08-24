"""Best-effort detection of which language a caller's transcribed words are
in — used only as a fallback hint for the voice bridge's language-selection
turn (see adapter.py's handling of the first
``conversation.item.input_audio_transcription.completed`` event of a call).

Three layers, cheapest and most certain first:

1. Direct name match — free, instant. A caller very often just states the
   language ("Marathi", "Hindi please") rather than speaking a sentence in
   it; asking an LLM "what language is this text in" for a bare name was
   observed to answer inconsistently run to run (the word "Marathi" is,
   as English spelling, arguably an English word), so a plain match against
   the candidate list is resolved here before either layer below runs.
2. Unicode script ranges — free, instant, no API call. Several of India's
   22 scheduled languages have a script no other listed language uses, so a
   handful of caller words is enough to identify them with certainty.
   Doesn't help for the languages that *share* a script (Hindi/Marathi/
   Nepali/Sanskrit/Maithili/Bodo/Konkani/Dogri all use Devanagari; English
   and Hinglish are both Latin) — those fall through to layer 3.
3. One classification call through the OpenAI chat-completions client this
   codebase already depends on (``core/llm.py``) — no new provider/library,
   just reuses the existing connection to ask "what language is this?" for
   the cases the layers above can't resolve.

Not meant to be exhaustive or authoritative: the caller's own stated answer
(parsed by the Realtime model itself from the conversation) is still the
primary mechanism. This only helps when that answer is ambiguous.
"""

from __future__ import annotations

import structlog

from apps.api.core.llm import chat_completion

logger = structlog.get_logger(__name__)

# (start, end) inclusive Unicode codepoint ranges that belong to a script
# unique to one language in our target set. Order doesn't matter — each
# range maps to exactly one language.
_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F),
    "Kannada": (0x0C80, 0x0CFF),
    "Malayalam": (0x0D00, 0x0D7F),
    "Gujarati": (0x0A80, 0x0AFF),
    "Punjabi": (0x0A00, 0x0A7F),  # Gurmukhi
    "Odia": (0x0B00, 0x0B7F),
    "Manipuri": (0xABC0, 0xABFF),  # Meetei Mayek
    "Santali": (0x1C50, 0x1C7F),  # Ol Chiki
}
# Arabic script is shared by several languages worldwide, but within our
# caller base (India) it's overwhelmingly Urdu — Kashmiri/Sindhi can also be
# written Perso-Arabic, but ambiguously enough that we defer to the LLM
# layer for those rather than guessing here.
_ARABIC_RANGE = (0x0600, 0x06FF)
# Bengali and Assamese share a Unicode block, but Assamese uses two letters
# Bengali doesn't: ৰ (ra, U+09F0) and ৱ (wa, U+09F1). Their presence is a
# reliable Assamese signal; their absence is only a weak signal for Bengali
# (an Assamese sentence that happens not to use either letter would still
# read as Bengali here) but is the reasonable default given Bengali is far
# more common among callers.
_BENGALI_RANGE = (0x0980, 0x09FF)
_ASSAMESE_ONLY_CHARS = {0x09F0, 0x09F1}
# Devanagari is shared by Hindi, Marathi, Nepali, Sanskrit, Maithili, Bodo,
# Konkani, and Dogri — script alone can't tell these apart.
_DEVANAGARI_RANGE = (0x0900, 0x097F)

_CANDIDATE_LANGUAGES = [
    "Hindi", "English", "Hinglish", "Tamil", "Telugu", "Kannada", "Malayalam",
    "Marathi", "Gujarati", "Punjabi", "Bengali", "Odia", "Urdu", "Assamese",
    "Bodo", "Dogri", "Kashmiri", "Konkani", "Maithili", "Manipuri", "Nepali",
    "Sanskrit", "Santali", "Sindhi",
]


def detect_from_name(text: str) -> str | None:
    """The caller directly *named* a language ("Marathi", "Hindi please")
    rather than spoke a sentence in one. Checked before the LLM layer: asking
    an LLM "what language is this text in" is the wrong question for a bare
    language name (the word "Marathi" is, as English orthography, arguably
    an English word) and was observed to answer inconsistently run to run -
    a plain name match is unambiguous and free, so resolve it here first.

    Only short-circuits when exactly one candidate name appears. Text naming
    two ("thoda hindi thoda english chalega" - "a bit of Hindi, a bit of
    English") is itself a mixed-language signal, not a single clean answer -
    that's left to the LLM layer, which already handles it correctly (reads
    as Hinglish) instead of us grabbing whichever name came first."""
    cleaned = text.strip().strip(".!?").casefold()
    if not cleaned:
        return None
    words = cleaned.split()
    matches = {
        lang for lang in _CANDIDATE_LANGUAGES
        if cleaned == lang.casefold() or lang.casefold() in words
    }
    if len(matches) == 1:
        return matches.pop()
    return None


def detect_from_script(text: str) -> str | None:
    """Identify the language from Unicode script alone, if the script is
    unique to one candidate language. Returns None if the text is empty, in
    a script shared by multiple candidates (Devanagari, Latin), or in a
    script not covered here at all."""
    if not text:
        return None

    for ch in text:
        code = ord(ch)
        for lang, (lo, hi) in _SCRIPT_RANGES.items():
            if lo <= code <= hi:
                return lang
        if _ARABIC_RANGE[0] <= code <= _ARABIC_RANGE[1]:
            return "Urdu"
        if _BENGALI_RANGE[0] <= code <= _BENGALI_RANGE[1]:
            has_assamese_letter = any(
                _BENGALI_RANGE[0] <= ord(c) <= _BENGALI_RANGE[1] and ord(c) in _ASSAMESE_ONLY_CHARS
                for c in text
            )
            return "Assamese" if has_assamese_letter else "Bengali"

    return None


async def classify_via_llm(text: str) -> str | None:
    """Ask the chat-completions model (already a dependency of this
    codebase, no new provider) which language the text is in. Returns None
    if the model can't tell or the call fails — never raises, since this is
    a best-effort hint, not something a call should drop for."""
    if not text.strip():
        return None

    prompt = (
        "Identify which language this transcribed phone-call snippet is in. "
        "Reply with exactly one word: one of "
        f"{', '.join(_CANDIDATE_LANGUAGES)}, or Unclear if you genuinely "
        "can't tell. No punctuation, no explanation.\n\n"
        f'Snippet: "{text.strip()}"'
    )
    try:
        result = await chat_completion([{"role": "user", "content": prompt}])
    except Exception:  # noqa: BLE001
        logger.warning("language_classify_llm_failed", exc_info=True)
        return None

    guess = (result.content or "").strip().strip(".")
    for lang in _CANDIDATE_LANGUAGES:
        if guess.casefold() == lang.casefold():
            return lang
    return None


async def detect_caller_language(text: str) -> str | None:
    """Combined detector, cheapest and most certain signal first: a directly
    named language, then script, then the LLM fallback for whatever's left
    (Devanagari group, Latin/English-Hinglish, or an unrecognized script)."""
    from_name = detect_from_name(text)
    if from_name is not None:
        return from_name
    from_script = detect_from_script(text)
    if from_script is not None:
        return from_script
    return await classify_via_llm(text)
