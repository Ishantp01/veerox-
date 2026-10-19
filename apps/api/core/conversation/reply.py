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

from apps.api.core.conversation.config import ScenarioDef, scenario_guidance
from apps.api.core.conversation.types import Decision, Understanding
from apps.api.core.conversation.understand import LLMCall

logger = structlog.get_logger(__name__)

# kind -> language -> text. {when} = "YYYY-MM-DD HH:MM" callback slot.
_TEMPLATES: dict[str, dict[str, str]] = {
    "greeting": {
        "en": "Hello{who}! How can I help you today?",
        "hinglish": "Namaste{who}! Main aapki kya madad kar sakta hoon?",
        "hi": "नमस्ते{who}! मैं आपकी क्या मदद कर सकता हूँ?",
    },
    "greeting_again": {
        "en": "I'm here. What would you like to know?",
        "hinglish": "Main yahin hoon. Aap kya jaanna chahenge?",
        "hi": "मैं यहीं हूँ। आप क्या जानना चाहेंगे?",
    },
    "ask_callback_time": {
        "en": "No problem. When would be a good time for us to call you back?",
        "hinglish": "Koi baat nahi. Hum aapko kab call karein? Bas time bata dijiye.",
        "hi": "कोई बात नहीं। हम आपको कब कॉल करें? बस समय बता दीजिए।",
    },
    "callback_confirmed": {
        "en": "Done, we'll call you back on {when}. Thank you{who}!",
        "hinglish": "Theek hai, hum aapko {when} par call karenge. Dhanyavaad{who}!",
        "hi": "ठीक है, हम आपको {when} पर कॉल करेंगे। धन्यवाद{who}!",
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
    "ask_appointment_time": {
        "en": "Sure, I can book that. Which day and time suits you?",
        "hinglish": "Zaroor, main book kar deta hoon. Aapko kaunsa din aur time theek rahega?",
        "hi": "ज़रूर, मैं बुक कर देता हूँ। आपको कौन सा दिन और समय ठीक रहेगा?",
    },
    "appointment_confirmed": {
        "en": "Done, your appointment is booked for {when}. Thank you!",
        "hinglish": "Ho gaya, aapki appointment {when} ke liye book ho gayi hai. Dhanyavaad!",
        "hi": "हो गया, आपकी अपॉइंटमेंट {when} के लिए बुक हो गई है। धन्यवाद!",
    },
    "whatsapp_sent": {
        "en": "I've sent the details to your WhatsApp. Please have a look.",
        "hinglish": "Maine details aapke WhatsApp par bhej di hain. Please dekh lijiye.",
        "hi": "मैंने विवरण आपके WhatsApp पर भेज दिए हैं। कृपया देख लीजिए।",
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
    "identification": {
        "en": "Thank you{who}! May I ask what kind of business you run?",
        "hinglish": "Shukriya{who}! Kya main puch sakta hoon ki aap kaunsa business karte hain?",
        "hi": "धन्यवाद{who}! क्या मैं पूछ सकता हूँ कि आप कौन सा बिज़नेस करते हैं?",
    },
    "discovery_business": {
        "en": "Got it{who}. What kind of business do you run?",
        "hinglish": "Samajh gaya{who}. Aap kaunsa business karte hain?",
        "hi": "समझ गया{who}। आप कौन सा बिज़नेस करते हैं?",
    },
    "discovery_pain": {
        "en": "Thanks for sharing{who}. What is the biggest challenge you face with it right now?",
        "hinglish": "Batane ke liye shukriya{who}. Abhi aapko sabse badi dikkat kya aa rahi hai?",
        "hi": "बताने के लिए धन्यवाद{who}। अभी आपको सबसे बड़ी परेशानी क्या आ रही है?",
    },
    "discovery_offer": {
        "en": "Thanks{who}, that is something we can help with. Shall we set up a quick call to go through it?",
        "hinglish": "Shukriya{who}, isme hum madad kar sakte hain. Kya hum ek quick call set kar dein?",
        "hi": "धन्यवाद{who}, इसमें हम मदद कर सकते हैं। क्या हम एक छोटा कॉल तय कर दें?",
    },
    "discount_followup": {
        "en": "I can't promise a discount myself, but our team can share the best offer. When is a good time for a quick call?",
        "hinglish": "Discount ka vaada main khud nahi kar sakta, lekin hamari team best offer bata sakti hai. Aapko kab call karein?",
        "hi": "छूट का वादा मैं खुद नहीं कर सकता, लेकिन हमारी टीम सबसे अच्छा ऑफर बता सकती है। आपको कब कॉल करें?",
    },
    "closing": {
        "en": "Thank you{who}! Have a great day.",
        "hinglish": "Dhanyavaad{who}! Aapka din shubh ho.",
        "hi": "धन्यवाद{who}! आपका दिन शुभ हो।",
    },
    "clarify": {
        "en": "Sorry, I want to be sure I understood. Could you tell me a little more about what you need?",
        "hinglish": "Maaf kijiye, main sahi se samajhna chahta hoon. Aap thoda aur bata sakte hain ki aapko kya chahiye?",
        "hi": "क्षमा करें, मैं सही से समझना चाहता हूँ। क्या आप थोड़ा और बता सकते हैं कि आपको क्या चाहिए?",
    },
    "reassure_no_pressure": {
        "en": "Of course, there is no pressure at all. Take your time to decide, and ask me anything you would like to know.",
        "hinglish": "Bilkul, koi pressure nahi hai. Aap apne hisaab se decide kijiye, aur jo poochna ho zaroor poochiye.",
        "hi": "बिल्कुल, कोई दबाव नहीं है। आप अपने हिसाब से तय कीजिए, और जो पूछना हो ज़रूर पूछिए।",
    },
    "hesitation_support": {
        "en": "I understand, it is a big decision and there is no hurry. Is there anything I can explain that would help?",
        "hinglish": "Main samajh sakta hoon, yeh bada faisla hai aur koi jaldi nahi hai. Kya main kuch samjha sakta hoon jo madad kare?",
        "hi": "मैं समझ सकता हूँ, यह बड़ा फ़ैसला है और कोई जल्दी नहीं है। क्या मैं कुछ समझा सकता हूँ जिससे मदद मिले?",
    },
    "confirm_correction": {
        "en": "Sorry for the mix-up. Earlier I noted \"{old}\", and now it is \"{new}\". Is that right?",
        "hinglish": "Maaf kijiye, gadbad ho gayi. Pehle maine \"{old}\" likha tha, ab \"{new}\" hai. Sahi hai na?",
        "hi": "क्षमा करें, गड़बड़ हो गई। पहले मैंने \"{old}\" लिखा था, अब \"{new}\" है। सही है न?",
    },
    "clarify_conflict": {
        "en": "Just to be sure: earlier I noted \"{old}\" and now you said \"{new}\". Which one is correct?",
        "hinglish": "Bas confirm karna hai: pehle \"{old}\" noted tha aur ab aapne \"{new}\" bataya. Kaun sa sahi hai?",
        "hi": "बस पुष्टि करनी है: पहले \"{old}\" नोट था और अब आपने \"{new}\" बताया। कौन सा सही है?",
    },
    "flexible_followup": {
        "en": "No problem at all. Which other day or time would suit you better?",
        "hinglish": "Koi baat nahi. Aapko kaunsa aur din ya time suit karega?",
        "hi": "कोई बात नहीं। आपको कौन सा और दिन या समय ठीक रहेगा?",
    },
    "soft_next_step": {
        "en": "Whenever you are ready, I can share more details or we can talk further. What would you like to know?",
        "hinglish": "Jab aap ready hon, main aur details bata sakta hoon ya hum aage baat kar sakte hain. Aap kya jaanna chahenge?",
        "hi": "जब आप तैयार हों, मैं और जानकारी दे सकता हूँ या हम आगे बात कर सकते हैं। आप क्या जानना चाहेंगे?",
    },
    "online_meeting_ok": {
        "en": "Sure, an online meeting works fine. Let me know whenever you would like to plan it.",
        "hinglish": "Ji bilkul, online meeting ho sakti hai. Jab aap plan karna chahein, bata dijiye.",
        "hi": "जी बिल्कुल, ऑनलाइन मीटिंग हो सकती है। जब आप प्लान करना चाहें, बता दीजिए।",
    },
    "honest_unknown_repeat": {
        "en": "I do not have a confirmed answer on that, and I have already passed your question to our team. Is there anything else I can help with?",
        "hinglish": "Iska confirmed jawab mere paas nahi hai, aur aapka sawaal main hamari team ko pehle hi de chuka hoon. Kya main aur kuch madad kar sakta hoon?",
        "hi": "इसका पक्का जवाब मेरे पास नहीं है, और आपका सवाल मैं हमारी टीम को पहले ही दे चुका हूँ। क्या मैं और कुछ मदद कर सकता हूँ?",
    },
    "repetition_apology": {
        "en": "Sorry about that, you already told me. Let us move on: what would you like to know?",
        "hinglish": "Maaf kijiye, aap pehle bata chuke hain. Aage badhte hain: aap kya jaanna chahenge?",
        "hi": "क्षमा करें, आप पहले बता चुके हैं। आगे बढ़ते हैं: आप क्या जानना चाहेंगे?",
    },
    "identify_self": {
        "en": "I am an AI assistant calling on behalf of the business you enquired with. Is this a good time to talk?",
        "hinglish": "Main ek AI assistant hoon, us business ki taraf se call kar raha hoon jisse aapne baat ki thi. Kya abhi baat karne ka sahi time hai?",
        "hi": "मैं एक AI असिस्टेंट हूँ, उस बिज़नेस की ओर से कॉल कर रहा हूँ जिससे आपने बात की थी। क्या अभी बात करने का सही समय है?",
    },
    "guided": {
        "en": "I understand. Let me help you with that.",
        "hinglish": "Main samajh gaya. Main isme aapki madad karta hoon.",
        "hi": "मैं समझ गया। मैं इसमें आपकी मदद करता हूँ।",
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


def _who(memory: dict[str, str] | None) -> str:
    """" Asha" when we remember the customer's name, else an empty string. Once a reply has
    used the name (enhanced dialogue records that as ``name_used``) it is not repeated."""
    name = (memory or {}).get("name")
    if (memory or {}).get("name_used"):
        return ""
    return f" {name}" if name else ""


def _already_replied(history: list[dict[str, Any]] | None) -> bool:
    return any(m.get("role") == "assistant" and m.get("content") for m in history or [])


def _context_block(history: list[dict[str, Any]] | None, memory: dict[str, str] | None) -> str:
    """Compact conversation context for the phrasing model: what we already
    know about the customer and the last few messages."""
    parts: list[str] = []
    known = {k: v for k, v in (memory or {}).items() if v}
    if known:
        parts.append("Known about the customer: " + "; ".join(f"{k}: {v}" for k, v in known.items()))
    recent = [
        f"{m['role']}: {str(m.get('content') or '')[:200]}"
        for m in (history or [])[-4:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if recent:
        parts.append("Recent conversation:\n" + "\n".join(recent))
    return "\n".join(parts)


def _facts_block(knowledge: list[Any]) -> str:
    return "\n".join(f"- {item.title}: {item.body}" for item in knowledge)


def _extractive(knowledge: list[Any]) -> str:
    return knowledge[0].body.strip()[:_MAX_FALLBACK_CHARS]


async def _guided_reply(
    decision: Decision,
    scenario_def: ScenarioDef | None,
    *,
    language: str,
    text: str,
    llm: LLMCall | None,
    timeout: float,
    history: list[dict[str, Any]] | None,
    memory: dict[str, str] | None,
) -> tuple[str, int, int]:
    """Reply for a scenario the business defined (or a built-in whose action was
    switched off): the model phrases it from the scenario's goal / do / avoid and
    the approved facts only. Without a model the admin's example reply (or a
    neutral line) is used, so a reply is always available."""
    fallback = (scenario_def.reply_example if scenario_def else "").strip() or _template(
        "guided", language
    )
    if llm is None:
        return fallback, 0, 0
    parts = [
        f"Reply in {_LANGUAGE_NAME.get(language, 'English')}, in at most two short sentences suited "
        "to being read aloud. Follow the situation guidance below. Never invent prices, dates, "
        "offers or promises, and never claim an action was done unless told so."
    ]
    guidance = scenario_guidance(scenario_def)
    if guidance:
        parts.append(f"Situation guidance: {guidance}")
    if scenario_def and scenario_def.reply_example:
        parts.append(f"Example of the wanted style (do not copy exactly): {scenario_def.reply_example}")
    if decision.knowledge:
        parts.append("Approved facts you may use:\n" + _facts_block(decision.knowledge))
    context = _context_block(history, memory)
    if context:
        parts.append(
            "Do not repeat what the customer was already told and do not ask for things already "
            f"known.\n{context}"
        )
    messages = [
        {"role": "system", "content": "\n\n".join(parts)},
        {"role": "user", "content": text},
    ]
    try:
        result = await asyncio.wait_for(llm(messages), timeout=timeout)
        phrased = (result.content or "").strip()
        return (phrased or fallback), result.tokens_in, result.tokens_out
    except Exception:  # noqa: BLE001 — keep the safe fallback line
        logger.warning("conversation_guided_reply_failed", exc_info=True)
        return fallback, 0, 0


async def build_reply(
    decision: Decision,
    understanding: Understanding,
    *,
    text: str,
    llm: LLMCall | None,
    timeout: float,
    history: list[dict[str, Any]] | None = None,
    memory: dict[str, str] | None = None,
    scenario_def: ScenarioDef | None = None,
) -> tuple[str, int, int]:
    """(reply, tokens_in, tokens_out). ``llm=None`` => no model call (shadow)."""
    language = understanding.language
    if decision.reply_kind == "guided":
        return await _guided_reply(
            decision,
            scenario_def,
            language=language,
            text=text,
            llm=llm,
            timeout=timeout,
            history=history,
            memory=memory,
        )
    if decision.reply_kind == "answer":
        reply = _extractive(decision.knowledge)
        tokens_in = tokens_out = 0
        if llm is not None:
            context = _context_block(history, memory)
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"Reply in {_LANGUAGE_NAME.get(language, 'English')}, in at most two short "
                        "sentences suited to being read aloud. Use ONLY the approved facts below. "
                        "Do not add prices, dates, promises or details that are not in them. If they "
                        "do not fully answer the question, say our team will confirm the rest.\n\n"
                        f"Approved facts:\n{_facts_block(decision.knowledge)}"
                        + (
                            "\n\nUse the context below so the reply fits the conversation: do not "
                            "repeat what the customer was already told, and do not ask for things "
                            f"already known.\n{context}"
                            if context
                            else ""
                        )
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
        if understanding.scenario == "interested" and decision.push:
            reply += _NEXT_STEP.get(language, _NEXT_STEP["en"])
        return reply, tokens_in, tokens_out

    when = ""
    if decision.reply_kind in ("callback_confirmed", "appointment_confirmed"):
        when = f"{decision.args['date']} {decision.args['time']}"
    kind = decision.reply_kind
    if kind == "greeting" and _already_replied(history):
        kind = "greeting_again"  # the conversation is already under way
    return (
        _template(
            kind,
            language,
            when=when,
            who=_who(memory),
            old=str(decision.args.get("old", "")),
            new=str(decision.args.get("new", "")),
        ),
        0,
        0,
    )
