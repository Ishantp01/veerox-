"""Process 5.0 — phrase the permitted reply.

Fixed scenarios use short, voice-friendly templates in Hindi / English /
Hinglish. Only knowledge answers use the LLM, and only to *phrase* the
approved facts it is handed; if that call fails the first approved item is
used verbatim (trimmed), so a reply is always available without guessing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.understand import LLMCall

logger = structlog.get_logger(__name__)

# kind -> language -> text. {when} = "YYYY-MM-DD HH:MM" callback slot.
_TEMPLATES: dict[str, dict[str, str]] = {
    "greeting": {
        "en": "Hello! How can I help you today?",
        "hinglish": "Namaste! Main aapki kya madad kar sakta hoon?",
        "hi": "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?",
    },
    "ask_callback_time": {
        "en": "No problem. When would be a good time for us to call you back?",
        "hinglish": "Koi baat nahi. Hum aapko kab call karein? Bas time bata dijiye.",
        "hi": "कोई बात नहीं। हम आपको कब कॉल करें? बस समय बता दीजिए।",
    },
    "callback_confirmed": {
        "en": "Done, we'll call you back on {when}. Thank you!",
        "hinglish": "Theek hai, hum aapko {when} par call karenge. Dhanyavaad!",
        "hi": "ठीक है, हम आपको {when} पर कॉल करेंगे। धन्यवाद!",
    },
    "handoff": {
        "en": "Sure, I've asked a team member to get in touch with you shortly.",
        "hinglish": "Zaroor, maine hamari team ke ek member ko aapse jaldi sampark karne ko kaha hai.",
        "hi": "ज़रूर, मैंने हमारी टीम के एक सदस्य से आपसे जल्द संपर्क करने को कहा है।",
    },
    "angry_handoff": {
        "en": "I'm really sorry about this. I've asked a team member to contact you right away.",
        "hinglish": "Mujhe iske liye bahut khed hai. Maine hamari team ko turant aapse sampark karne ko kaha hai.",
        "hi": "मुझे इसके लिए बहुत खेद है। मैंने हमारी टीम को तुरंत आपसे संपर्क करने को कहा है।",
    },
    "not_interested": {
        "en": "Understood, thank you for your time. We won't disturb you further.",
        "hinglish": "Samajh gaya, aapke samay ke liye dhanyavaad. Hum aapko aur pareshan nahi karenge.",
        "hi": "समझ गया, आपके समय के लिए धन्यवाद। हम आपको और परेशान नहीं करेंगे।",
    },
    "unknown_followup": {
        "en": "I want to give you the right answer, so I've asked our team to confirm and get back to you.",
        "hinglish": "Main aapko sahi jawab dena chahta hoon, isliye maine hamari team se confirm karke aapko batane ko kaha hai.",
        "hi": "मैं आपको सही जवाब देना चाहता हूँ, इसलिए मैंने हमारी टीम से पुष्टि करके आपको बताने को कहा है।",
    },
    "interested": {
        "en": "Great! When would be a good time for a quick call to go through the details?",
        "hinglish": "Bahut badhiya! Details samjhane ke liye hum aapko kab call karein?",
        "hi": "बहुत बढ़िया! विवरण समझाने के लिए हम आपको कब कॉल करें?",
    },
    "objection_followup": {
        "en": "I understand. Our team can go through the options with you. When would be a good time for a quick call?",
        "hinglish": "Main samajh sakta hoon. Hamari team aapke saath options par baat kar sakti hai. Aapko kab call karein?",
        "hi": "मैं समझ सकता हूँ। हमारी टीम आपके साथ विकल्पों पर बात कर सकती है। आपको कब कॉल करें?",
    },
    "confused": {
        "en": "Sorry for the confusion. Could you tell me a little more about what you'd like to know?",
        "hinglish": "Confusion ke liye maaf kijiye. Aap thoda aur bata dijiye ki aap kya jaanna chahte hain?",
        "hi": "भ्रम के लिए क्षमा करें। आप थोड़ा और बता दीजिए कि आप क्या जानना चाहते हैं?",
    },
}

_NEXT_STEP = {
    "en": " Shall we set up a quick call to take this forward?",
    "hinglish": " Kya hum aage baat karne ke liye ek quick call set kar dein?",
    "hi": " क्या हम आगे बात करने के लिए एक छोटा कॉल तय कर दें?",
}

_LANGUAGE_NAME = {"en": "English", "hi": "Hindi (Devanagari)", "hinglish": "Hinglish (Hindi in Latin script)"}

_MAX_FALLBACK_CHARS = 320


def _template(kind: str, language: str, **fmt: str) -> str:
    by_language = _TEMPLATES[kind]
    return by_language.get(language, by_language["en"]).format(**fmt)


def _facts_block(knowledge: list[Any]) -> str:
    return "\n".join(f"- {item.title}: {item.body}" for item in knowledge)


def _extractive(knowledge: list[Any]) -> str:
    return knowledge[0].body.strip()[:_MAX_FALLBACK_CHARS]


async def build_reply(
    decision: Decision,
    understanding: Understanding,
    *,
    text: str,
    llm: LLMCall | None,
    timeout: float,
) -> tuple[str, int, int]:
    """(reply, tokens_in, tokens_out). ``llm=None`` => no model call (shadow)."""
    language = understanding.language
    if decision.reply_kind == "answer":
        reply = _extractive(decision.knowledge)
        tokens_in = tokens_out = 0
        if llm is not None:
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"Reply in {_LANGUAGE_NAME.get(language, 'English')}, in at most two short "
                        "sentences suited to being read aloud. Use ONLY the approved facts below. "
                        "Do not add prices, dates, promises or details that are not in them. If they "
                        "do not fully answer the question, say our team will confirm the rest.\n\n"
                        f"Approved facts:\n{_facts_block(decision.knowledge)}"
                    ),
                },
                {"role": "user", "content": text},
            ]
            try:
                result = await asyncio.wait_for(llm(messages), timeout=timeout)
                phrased = (result.content or "").strip()
                if phrased:
                    reply = phrased
                tokens_in, tokens_out = result.tokens_in, result.tokens_out
            except Exception:  # noqa: BLE001 — keep the extractive reply
                logger.warning("conversation_phrase_failed", exc_info=True)
        if understanding.scenario == "interested":
            reply += _NEXT_STEP.get(language, _NEXT_STEP["en"])
        return reply, tokens_in, tokens_out

    when = ""
    if decision.reply_kind == "callback_confirmed":
        when = f"{decision.args['date']} {decision.args['time']}"
    return _template(decision.reply_kind, language, when=when), 0, 0
