"""Enhanced dialogue policy (Phase 4): reading the customer's *situation*, not
just their scenario.

The base engine maps a message to one scenario and answers it. Real sales
conversations are messier: a customer is interested but undecided, asks about
investment and profit, corrects something they said earlier, prefers an online
meeting, worries about being pressured and is travelling tomorrow, all in a few
turns. This module adds what the base engine lacks, and nothing else:

* ``analyse`` reads the text for signals (hesitation, pressure worry,
  correction, conflict, meeting preference, unavailability, a declined time, a
  repetition complaint, direct questions and what they are about) and for the
  facts that go with them, in English, Hindi and Hinglish. Rule-based: no model
  call, no latency.
* ``enhance_understanding`` attaches those to an ``Understanding`` and keeps
  verified facts from being overwritten by uncertain speech.
* ``enhance_decision`` is the policy: answer a direct question before anything
  else, never invent an answer, never push for an appointment the customer is
  not ready for, never book on a guessed or declined time, confirm a correction,
  ask when two statements conflict, don't repeat what was already answered.
* ``memory_updates`` is what to remember afterwards (call-scoped counters and
  the new facts), so the next turn can avoid repeating itself.
* ``disposition_for`` names the lead outcome the turn implies.

It only runs for an org that opted in (``EngineConfig.enhanced_dialogue``);
without that every function here is unreachable and the engine behaves exactly
as before. It is pure: no I/O, no model, no clock except the ``today`` it is
handed. Nothing it produces is passed to a tool except the same validated
callback / appointment arguments the base engine already uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from apps.api.core.conversation.types import (
    STATE_AWAITING_APPOINTMENT_TIME,
    STATE_AWAITING_CALLBACK_TIME,
    STATE_ENGAGED,
    STATE_OBJECTION,
    Decision,
    Understanding,
)

# --- vocabulary ---------------------------------------------------------------------------------------------

# Signals on Understanding.signals. Topics are "topic:<name>".
HESITATION = "hesitation"
PRESSURE = "pressure_concern"
NO_COMMITMENT = "wants_no_commitment"
CORRECTION = "correction"
CONFLICT = "conflict"
PREFERS_ONLINE = "prefers_online"
PREFERS_IN_PERSON = "prefers_in_person"
UNAVAILABLE = "unavailable"
DECLINES_TIME = "declines_time"
REPETITION = "repetition_complaint"
END_CALL = "end_call"
ASKS_IDENTITY = "asks_identity"
DIRECT_QUESTION = "direct_question"
DECLINES_MEETING = "declines_meeting"
DATE_CORRECTION = "date_correction"
ASKS_TIME = "asks_for_time"
# A previously given detail was replaced by a new value: absorbed silently, never a hedge.
SLOT_UPDATED = "slot_updated"

# Memory keys this module adds (kept out of MEMORY_KEYS so orgs that have not
# opted in never store them; see memory.save_facts(allowed_keys=...)).
ENHANCED_MEMORY_KEYS = (
    "property_info",
    "property_info_pending",
    "investment_budget",
    "investment_budget_pending",
    "customer_background",
    "meeting_preference",
    "unavailable_dates",
    "appointment_asks",
    "answered_topics",
    "handoff_requested",
    "pressure_concern",
    "name_used",
    "customer_city",
    "meeting_declined",
    "ack_last",
)

# Facts that, once given, are only replaced when the customer clearly corrects
# them (or states the same new value twice), never by one differing mention.
_GUARDED_FACTS = ("property_info", "investment_budget")
# Base-engine facts protected the same way.
_PROTECTED_BASE_FACTS = ("name", "business_type", "team_size")

_HEDGE_FLAGS = frozenset(
    {HESITATION, PRESSURE, NO_COMMITMENT, UNAVAILABLE, DECLINES_TIME, CORRECTION, CONFLICT, REPETITION,
     DECLINES_MEETING, ASKS_TIME}
)

# Reply kinds that push the customer toward a callback / appointment.
PUSH_KINDS = frozenset({"interested", "discovery_offer", "objection_followup", "discount_followup"})
# Scenarios the policy never touches: they end in a person or an ending.
_ESCALATION_SCENARIOS = frozenset({"human_handoff", "angry", "not_interested", "closing"})
_ANSWERABLE_KINDS = frozenset(
    {
        "answer", "interested", "discovery_offer", "objection_followup", "discount_followup",
        "confused", "clarify", "unknown_followup", "guided", "greeting", "discovery_business",
        "discovery_pain", "identification",
    }
)
# New reply kinds (templates in reply.py, spoken instructions in voice_decision.py).
NEW_REPLY_KINDS = (
    "reassure_no_pressure", "hesitation_support", "confirm_correction", "clarify_conflict",
    "flexible_followup", "soft_next_step", "online_meeting_ok", "honest_unknown_repeat",
    "repetition_apology", "identify_self", "confirm_day_correction", "give_time",
)
# Kinds whose wording greets the customer by name.
_NAMING_KINDS = frozenset(
    {"greeting", "callback_confirmed", "identification", "discovery_business", "discovery_pain",
     "discovery_offer", "closing"}
)

# --- patterns ------------------------------------------------------------------------------------------------

_HESITATION_RE = re.compile(
    r"(not (very )?sure|\bunsure\b|let me think|think about it|need (some |a little )?time|"
    r"haven'?t (yet )?decided|not decided|undecided|\bmaybe\b|i'?ll see|"
    r"dekhte hain|dekhta hoon|dekhti hoon|sochna|sochta|sochti|sochunga|sochungi|soch (ke|kar)|soch raha|"
    r"decide (nahi|nhi|nahin)|pakka (nahi|nhi|nahin)|sure (nahi|nhi|nahin)|pata nahi|confused|dilemma|"
    r"samajh (nahi|nhi|nahin) aa|samajh (nahi|nhi|nahin) pa|(interested|interest) hoon,? ya (nahi|nhi|nahin)|"
    r"hoon ya (nahi|nhi|nahin)|hai ya (nahi|nhi|nahin)|"
    r"सोचना|सोचूंगा|सोचूँगा|देखते हैं|पक्का नहीं|तय नहीं)",
    re.IGNORECASE,
)
_PRESSURE_RE = re.compile(
    r"(pressur|pushy|pushing|forc(e|ing)|zabardasti|jabardasti|dabaav|dabav|peeche pad|"
    r"दबाव|ज़बरदस्ती|जबरदस्ती)",
    re.IGNORECASE,
)
_NO_COMMITMENT_RE = re.compile(
    r"(commit(ment|ted)?\b|obligation|bind(ing)?\b|bandhan|bound to|no strings|compulsion|compulsory|"
    r"कमिटमेंट|बाध्य)",
    re.IGNORECASE,
)
_CORRECTION_STRONG_RE = re.compile(
    r"(\bnahi nahi\b|\bnahin nahin\b|\bnhi nhi\b|\bno,? no\b|\bi meant\b|\bmy (mistake|bad)\b|"
    r"\bgalat\b|\bgalti\b|\bwrong\b|\bsorry,? (i|it|that|actually)\b|\bmaaf kijiye,? (main|maine)\b|"
    r"\bcorrection\b|\bnot that\b|मेरा मतलब|गलत|नहीं नहीं)",
    re.IGNORECASE,
)
_CORRECTION_WEAK_RE = re.compile(r"(\bactually\b|\brather\b|\bmatlab\b|असल में|दरअसल)", re.IGNORECASE)
# Needing to talk it over with someone is hesitation, not a no.
_CONSULT_RE = re.compile(
    r"((family|ghar|parivar|wife|husband|partner|papa|mummy|bhai|dost|accountant|\bca\b)\b.{0,30}"
    r"(discuss|baat|puch|consult|salah|sochna|sochenge)|"
    r"(discuss|consult)\w*\b.{0,25}\b(family|ghar|parivar|wife|husband|partner)|"
    r"घर.{0,20}(बात|चर्चा)|फैमिली.{0,20}(बात|डिस्कस))",
    re.IGNORECASE | re.DOTALL,
)
# They do not want a meeting, or they are tired of being pushed toward one. "meeting nahi ho payegi" is a
# time that does not work, not a refusal.
_DECLINE_MEETING_RE = re.compile(
    r"(\bmeeting\s+(?:nahi|nhi|nahin|mat)\b(?!\s+ho\s*(?:pa|sak))|\b(?:nahi|nhi|no)\s+meeting\b|"
    r"\bmeeting\s+ki\s+(?:zaroorat|jarurat|zarurat)\s+(?:nahi|nhi)|"
    r"\b(?:don'?t|do not)\s+(?:want|need)\s+(?:a\s+|any\s+)?(?:meeting|appointment)|"
    r"\bno\s+need\s+(?:for|of)\s+(?:a\s+)?(?:meeting|appointment)|"
    r"\b(?:meeting|appointment)\s+(?:karni|karna|karunga|karungi|chahiye)\s*(?:nahi|nhi)\b|"
    r"मीटिंग\s+(?:नहीं|नही)|नहीं\s+चाहिए\s+मीटिंग)",
    re.IGNORECASE,
)
_MEETING_PRESSURE_RE = re.compile(
    r"((baar[- ]baar|bar[- ]bar|kitni baar|again and again|har baar|keep (?:asking|saying|pushing))"
    r".{0,40}(meeting|appointment|milne|milna)|"
    r"(meeting|appointment|milne|milna).{0,40}(baar[- ]baar|bar[- ]bar|kitni baar|again and again))",
    re.IGNORECASE | re.DOTALL,
)
_DAYS = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|somvar|mangalvar|budhvar|guruvar|"
    r"shukravar|shanivar|ravivar|kal|parso|aaj|tomorrow|today)"
)
# "Nahi, Monday." / "I said Monday" / "not Tuesday, Monday": a correction of the day or time proposed.
_DAY_CORRECTION_RE = re.compile(
    rf"(^\W*(?:nahi|nhi|nahin|no|nope)\b[\s,.\-]*(?:\w+\s+){{0,2}}?{_DAYS}\b|"
    rf"\b(?:i said|maine (?:bola|kaha|bataya)(?: tha)?)\W+{_DAYS}\b|"
    rf"\bnot\s+{_DAYS}\W+(?:but\s+)?{_DAYS}\b)",
    re.IGNORECASE,
)
# Hedged statements are a guess, not a fact to keep.
_UNCERTAIN_RE = re.compile(
    r"(shayad|\bmaybe\b|\bperhaps\b|i guess|\bguess\b|\bmight\b|pata nahi|not sure|andaz[ae]|ho sakta hai|"
    r"शायद|पता नहीं)",
    re.IGNORECASE,
)
# A city only from "I am from / main <City> se hoon / I live in <City>", never from a bare capitalised word.
_CITY_WORD = r"(?-i:[A-Z][a-z]{2,19}(?:\s[A-Z][a-z]{2,19})?)"
_CITY_RES = (
    re.compile(
        rf"\b(?:main|mai|mein)\s+(?P<c>{_CITY_WORD})\s+(?:se\s+(?:hoon|hun|hu)\b|(?:mein|me)\s+(?:rehta|rehti|rahta|rahti)\b)",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(?:i am|i'm|i live|i stay|based)\s+(?:from|in|at)\s+(?P<c>{_CITY_WORD})\b", re.IGNORECASE),
    re.compile(rf"\b(?:mera|meri)\s+(?:shehar|sheher|city|town)\s+(?:hai\s+)?(?P<c>{_CITY_WORD})\b", re.IGNORECASE),
)
_NOT_PLACES = frozenset(
    "office home ghar shop dukaan bank company india indian sir madam ji haan hello okay sure fine theek "
    "thik accha achha baad abhi kal aaj phir thoda bahut india private limited pvt ltd store showroom".split()
)
# Words that follow "main" without being a name ("main baad mein call karunga" is not a person called Baad).
_NOT_NAMES = frozenset(
    "baad abhi kal aaj phir thoda bahut ek apne aapko aapse yahan wahan nahi nahin bilkul zaroor free busy "
    "driving office home ghar shop dukaan bank company india indian sir madam ji haan hello okay ok sure "
    "fine theek thik accha achha sahi bas kuch kabhi jab tab agar lekin magar kyunki isliye ab".split()
)
# A refusal of everything, as opposed to of a meeting.
_GENERAL_NO_RE = re.compile(
    r"(not interested|interested nahi|interest nahi|no interest|nahi lena|don'?t call|call mat|stop calling|"
    r"no thanks|rehne do|band karo)",
    re.IGNORECASE,
)
# "ek minute", "let me think", "ruko" at the END of a message: they asked for a moment, not for an answer.
_ASKS_TIME_RE = re.compile(
    r"(?:(?:ek|1)\s*(?:minute|min|second|sec|moment)|one\s+(?:minute|second|moment)|"
    r"just\s+a\s+(?:minute|second|sec|moment)|hold\s+on|give\s+me\s+a\s+(?:minute|second|moment)|"
    r"let\s+me\s+think|soch\s+(?:raha|rahi|rha|rhi)|sochne\s+do|ruko|ruk\s+jao|rukiye|"
    r"एक\s+(?:मिनट|सेकंड)|रुको|रुकिए)"
    r"[\s.,!…\-]*(?:ji|please|na|hai|hoon|ok|okay|sir|madam)?[\s.,!…\-]*$",
    re.IGNORECASE,
)
_ONLINE_RE = re.compile(
    r"(\b(online|virtual|video|zoom|google meet|gmeet|teams|remote)\b.{0,30}"
    r"\b(meeting|meet|call|baat|discussion|demo|session|baithak|milna|karte|kar lo|kar lete|kar sakte|prefer)\b|"
    r"\b(meeting|meet|call|baat|discussion|demo|session)\b.{0,30}\b(online|virtual|video|zoom|remote)\b|"
    r"\bprefer(s|red)?\b.{0,20}\b(online|video|zoom|virtual)\b|ऑनलाइन)",
    re.IGNORECASE | re.DOTALL,
)
_IN_PERSON_RE = re.compile(
    r"(in[- ]person|face[- ]to[- ]face|aamne[- ]saamne|office (aa|pe|par|mein|me)\b|mil (ke|kar)\b|"
    r"\bmilna\b|मिलकर|आमने-सामने)",
    re.IGNORECASE,
)
_DATE_WORDS = r"(kal|tomorrow|today|aaj|parso|next week|this week|is week|agle hafte|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_AWAY_VERBS = r"(karunga|karungi|kar raha|kar rahi|hoon|hun|\bhu\b|rahunga|rahungi|jaana|jana|jaunga|jaungi|will|going|am)"
_AWAY_WORDS = r"(travel\w*|trip|yatra|safar|tour|wedding|shaadi|shadi|outstation|out of (?:town|station)|bahar)"
_UNAVAILABLE_RE = re.compile(
    rf"(\b{_AWAY_WORDS}\b.{{0,25}}\b({_DATE_WORDS}|{_AWAY_VERBS})\b|"
    rf"\b({_DATE_WORDS}|{_AWAY_VERBS})\b.{{0,25}}\b{_AWAY_WORDS}\b|"
    r"not (be )?(available|free|around)|(available|free) (nahi|nhi|nahin)|"
    r"nahi (aa|mil) (sakta|sakti|paunga|paungi)|(kal|tomorrow|aaj|today|parso)\s+(nahi|nhi|not)\b|"
    r"यात्रा|बाहर (हूँ|हूं|जाना)|उपलब्ध नहीं|सफर)",
    re.IGNORECASE,
)
_DECLINE_TIME_RE = re.compile(
    r"(nahi ho (payega|paayega|sakta|sakega|pाएगा)|possible nahi|convenient nahi|suit nahi|"
    r"(us|iss|is) (time|din|slot) (par |pe )?(nahi|mushkil)|(doosra|dusra|koi aur) (time|din|slot)|"
    r"another (time|day|slot)|different (time|day|slot)|re-?schedule|"
    r"(that|this) (time|day|slot) (doesn'?t|does not|won'?t|will not) work|not (possible|convenient)|"
    r"समय नहीं|कोई और (समय|दिन))",
    re.IGNORECASE,
)
_REPETITION_RE = re.compile(
    r"(baar[- ]baar|bar[- ]bar|phir se (wahi|vahi)|(wahi|vahi) (baat|sawaal|sawal|question|cheez)|"
    r"same (thing|question|stuff)|why (do|are) you (keep|repeat)|already (told|said|answered|mentioned)|"
    r"maine (bata diya|bola tha|bataya tha)|pehle (bata|bol) (chuka|chuki|diya)|kitni baar|"
    r"बार-बार|कितनी बार)",
    re.IGNORECASE,
)
_END_CALL_RE = re.compile(
    r"(phone rakh|rakhta hoon|rakhti hoon|rakh raha hoon|call (end|cut|khatam)|disconnect|"
    r"please end|end (the )?call|ab bas\b|bas kijiye|that'?s all|i have to go|jaana hai ab|"
    r"फोन रख|कॉल खत्म)",
    re.IGNORECASE,
)
# An explicit instruction to hang up is unmistakable anywhere in the sentence...
_END_STRONG_RE = re.compile(
    r"(phone rakh|\bcall (end|cut|khatam)\b|\bdisconnect\b|\bplease end\b|\bend (the )?call\b|फोन रख|कॉल खत्म)",
    re.IGNORECASE,
)
# ...but "bye", "that's all", "ab bas", "I have to go" are only an ending when they ARE the utterance (maybe
# with a thank-you or "ok" around them): "ab bas thoda time chahiye" or "that's all fine, but what about the
# margin?" must not hang up on a customer who is still talking.
_END_FILLER = r"(?:ok(?:ay)?|theek hai|thik hai|accha|achha|ji|haan|thanks?|thank you|dhanyavaad|shukriya|sir|madam)"
_END_WEAK_CORE = (
    r"(?:that'?s all|that is all|bas(?: ab| itna hi| itna)?|ab bas|bye(?:[\s-]+bye)?|good ?bye|alvida|"
    r"i have to go|i must go|chalta hoon|chalti hoon|rakhta hoon|rakhti hoon|rakh raha hoon|rakh rahi hoon)"
)
_END_WEAK_RE = re.compile(
    rf"^\W*(?:{_END_FILLER}[\s,.!]+)*{_END_WEAK_CORE}"
    rf"(?:[\s,.!]+(?:{_END_FILLER}|{_END_WEAK_CORE}))*[\s.!]*$",
    re.IGNORECASE,
)


def looks_like_end_request(text: str) -> bool:
    """Is this the caller asking to end the call? Deliberately conservative: a false positive hangs up on
    a customer, a false negative only means the agent keeps listening (the silence handling and the
    agent's own judgement still end the call)."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    return bool(_END_STRONG_RE.search(stripped) or _END_WEAK_RE.match(stripped))


END_CALL_RE = _END_CALL_RE  # kept for compatibility; the voice adapter uses looks_like_end_request

_IDENTITY_RE = re.compile(
    r"(who (is|are) (this|you|calling)|who'?s (this|calling)|kaun (ho|hai|bol|bol raha|bol rahe|sa call)|"
    r"aap kaun|kahan se (bol|call)|kis (company|taraf se)|कौन (हो|है|बोल)|कहाँ से (बोल|कॉल))",
    re.IGNORECASE,
)
_QUESTION_WORD_RE = re.compile(
    r"(\?|\b(kya|kitna|kitne|kitni|kaise|kab|kyun|kyu|kaun|kahan|how|what|when|why|where|which|"
    r"can i|do i|will i|is it|are there|does it|hoga|hogi|milega)\b|क्या|कितना|कितने|कैसे|कब|क्यों|कौन|कहाँ)",
    re.IGNORECASE,
)
_BACKGROUND_GAP_RE = re.compile(
    r"((no|not any|zero|nahi|nhi|koi)\s+.{0,20}(background|experience|anubhav)|"
    r"(background|experience|anubhav)\b.{0,25}\b(nahi|nhi|no|not|zero)\b|first time|pehli baar|new to)",
    re.IGNORECASE,
)

_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "investment": re.compile(
        r"(invest|nivesh|lagat\b|lagana|lagega|lagegi|capital|\bfund|budget|deposit|security money|"
        r"setup cost|निवेश|लागत)",
        re.IGNORECASE,
    ),
    "profit": re.compile(
        r"(profit|margin|returns?\b|\broi\b|kamai|kamaayi|munafa|fayda|income|earning|payback|"
        r"मुनाफा|कमाई|फायदा|लाभ)",
        re.IGNORECASE,
    ),
    "commitment": re.compile(
        r"(commit(ment|ted)?\b|obligation|bind(ing)?\b|bandhan|compulsion|compulsory|कमिटमेंट|बाध्य)",
        re.IGNORECASE,
    ),
    "location": re.compile(
        r"(location|jagah|space|area\b|sq\.? ?ft|square f|acre|bigha|gaj\b|marla|property|plot\b|"
        r"zameen|जगह|प्रॉपर्टी|जमीन)",
        re.IGNORECASE,
    ),
    "timeline": re.compile(
        r"(kitne (din|mahine|time|saal)|how long|timeline|kab tak|duration|how soon|set ?up time)",
        re.IGNORECASE,
    ),
    "support": re.compile(
        r"(support|training|guidance|sahayata|stock supply|supply chain|maal|marketing help)", re.IGNORECASE
    ),
    "process": re.compile(
        r"(process|procedure|steps\b|how (to|do i|can i) (start|apply|get|join)|apply\b|"
        r"documents?\b|documentation|licen[cs]e|agreement|kaise (shuru|milega|join))",
        re.IGNORECASE,
    ),
    "experience": re.compile(
        r"(experience|background|anubhav|qualification|degree|pharma)", re.IGNORECASE
    ),
    # "How old is your company" is not a question about how long setting up takes.
    "company": re.compile(
        r"((company|firm|brand|organi[sz]ation|aapki|aapka|aap log)\b.{0,30}"
        r"(kitne saal|kitni purani|kitna purana|purani|purana|established|founded|since when|history|"
        r"kab (?:se|shuru|bani|start)|how old|how long (?:have|has)|years? (?:old|in business))|"
        r"kab se (?:chal|kaam|business)|कंपनी.{0,20}(पुरानी|साल))",
        re.IGNORECASE | re.DOTALL,
    ),
}
# Questions on these must never be answered with a number the business has not approved.
FIGURE_TOPICS = frozenset({"investment", "profit"})

_PROPERTY_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:acre|bigha|gaj|marla|kanal|sq\.? ?ft|sqft|square (?:feet|foot|yards?)|sq\.? ?yards?)|"
    r"\b(?:own(?:ed)?|rented|on rent|kiraye|kirayedaar|khud ki|apni)\b.{0,25}"
    r"\b(?:shop|dukaan|property|jagah|plot|space|building|godown|land)\b|"
    r"\b(?:shop|dukaan|property|jagah|plot|space|building|godown|land)\b.{0,25}"
    r"\b(?:own(?:ed)?|rented|on rent|kiraye|apni|khud ki)\b)",
    re.IGNORECASE,
)
_BUDGET_RE = re.compile(
    r"((?:₹|rs\.?|rupees?|inr)?\s*\d+(?:\.\d+)?\s*(?:lakh|lac|lakhs|crore|cr|thousand|hazaar|k)\b)",
    re.IGNORECASE,
)
_BUDGET_CONTEXT_RE = re.compile(
    r"(invest|budget|nivesh|capital|lagana|laga (sakta|sakti)|paas hai|tak|around|approx|lagbhag)", re.IGNORECASE
)
_CLAUSE_SPLIT_RE = re.compile(r"[,;.\n!?।]+|\b(?:lekin|but|magar|par|however)\b", re.IGNORECASE)

_MAX_FACT_LEN = 120


# --- analysis ------------------------------------------------------------------------------------------------------


@dataclass
class DialogueAnalysis:
    signals: frozenset[str] = frozenset()
    facts: dict[str, str] = field(default_factory=dict)
    # memory key -> the value the customer had given before (when this turn changes it)
    corrections: dict[str, str] = field(default_factory=dict)
    # memory key -> the value they had given before, when this turn contradicts
    # it WITHOUT saying it is a correction (the old value is kept until confirmed)
    conflicts: dict[str, str] = field(default_factory=dict)
    # True when this turn's date/time words describe when the customer is NOT
    # available: the base extractor's callback facts must not be trusted.
    drop_callback: bool = False
    # Callback facts re-derived from the clauses that are not about being unavailable.
    callback_facts: dict[str, str] = field(default_factory=dict)


_BARE_AMOUNT_WORDS = frozenset(
    "shayad maybe perhaps around approx about lagbhag hoga hogi hai tak ke aas paas ji haan nahi actually "
    "rupaye rupees rs inr budget mera meri apna approximately lagana".split()
)


def _tokens_lower(text: str) -> list[str]:
    return re.findall(r"[a-z0-9ऀ-ॿ]+", text.lower())


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c and c.strip()]


def detect_topics(text: str) -> tuple[str, ...]:
    found = [name for name, pattern in _TOPIC_PATTERNS.items() if pattern.search(text)]
    if "company" in found and "timeline" in found:
        found.remove("timeline")  # "kitne saal" here is the company's age
    return tuple(found)


def _city_in(text: str) -> str | None:
    for pattern in _CITY_RES:
        match = pattern.search(text)
        if match:
            city = match.group("c").strip()
            if not any(part.lower() in _NOT_PLACES for part in city.split()):
                return city[:40]
    return None


def _drop_misread_name(understanding: Understanding | None) -> Understanding | None:
    """The base rules read "main <word>" as a name: drop the ones that are plainly not names."""
    if understanding is None:
        return None
    name = understanding.facts.get("name")
    if name and name.strip().lower() in _NOT_NAMES:
        return replace(understanding, facts={k: v for k, v in understanding.facts.items() if k != "name"})
    return understanding


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip(" .,")


def _mentioned_dates(clause: str, today: date) -> list[str]:
    lowered = clause.lower()
    found: list[str] = []
    if re.search(r"\b(day after tomorrow|parso)\b|परसों", lowered):
        found.append((today + timedelta(days=2)).isoformat())
    elif re.search(r"\b(tomorrow|kal)\b|कल", lowered):
        found.append((today + timedelta(days=1)).isoformat())
    if re.search(r"\b(today|aaj)\b|आज", lowered):
        found.append(today.isoformat())
    return found


def analyse(text: str, *, memory: dict[str, str], today: date) -> DialogueAnalysis:
    """Signals and facts in one customer message. Never guesses: a fact is the
    customer's own words, and a signal needs an explicit cue."""
    signals: set[str] = set()
    facts: dict[str, str] = {}
    corrections: dict[str, str] = {}
    conflicts: dict[str, str] = {}
    overwritten: set[str] = set()

    if _HESITATION_RE.search(text) or _CONSULT_RE.search(text):
        signals.add(HESITATION)
    if _DECLINE_MEETING_RE.search(text) or _MEETING_PRESSURE_RE.search(text):
        signals.add(DECLINES_MEETING)
    if _ASKS_TIME_RE.search(text.strip()):
        signals.add(ASKS_TIME)
    if _PRESSURE_RE.search(text):
        signals.add(PRESSURE)
    if _NO_COMMITMENT_RE.search(text):
        signals.add(NO_COMMITMENT)
    if _REPETITION_RE.search(text):
        signals.add(REPETITION)
    if looks_like_end_request(text):
        signals.add(END_CALL)
    if _IDENTITY_RE.search(text):
        signals.add(ASKS_IDENTITY)
    if _ONLINE_RE.search(text):
        signals.add(PREFERS_ONLINE)
        facts["meeting_preference"] = "online"
    elif _IN_PERSON_RE.search(text):
        signals.add(PREFERS_IN_PERSON)
        facts["meeting_preference"] = "in_person"

    topics = detect_topics(text)
    asks = bool(_QUESTION_WORD_RE.search(text))
    if asks and topics:
        signals.add(DIRECT_QUESTION)
        signals.update(f"topic:{t}" for t in topics)

    city = _city_in(text)
    if city:
        facts["customer_city"] = city  # the latest statement wins: they may have moved or been misheard

    # --- facts the customer states -------------------------------------------------
    clauses = _clauses(text)
    candidates: dict[str, str] = {}
    property_clause = None
    for clause in clauses:
        if _PROPERTY_RE.search(clause):
            property_clause = clause
    if property_clause:
        candidates["property_info"] = property_clause[:_MAX_FACT_LEN]
    for clause in clauses:
        budget = _BUDGET_RE.search(clause)
        if budget and _BUDGET_CONTEXT_RE.search(clause):
            candidates["investment_budget"] = budget.group(1).strip()[:_MAX_FACT_LEN]
    for clause in clauses:
        if _BACKGROUND_GAP_RE.search(clause):
            facts["customer_background"] = clause[:_MAX_FACT_LEN]
            break

    # Guarded facts: a first statement is stored; a differing one is a correction
    # only when the customer says so (or repeats the same new value), otherwise it
    # is a conflict and the verified old value stays until they confirm.
    strong = bool(_CORRECTION_STRONG_RE.search(text))
    weak = bool(_CORRECTION_WEAK_RE.search(text))
    hedged = bool(_UNCERTAIN_RE.search(text))
    if (strong or weak) and memory.get("investment_budget") and "investment_budget" not in candidates:
        # "Nahi, actually 7 lakh": a correction carries its own context, so the amount alone is enough.
        for clause in clauses:
            amount = _BUDGET_RE.search(clause)
            if amount:
                candidates["investment_budget"] = amount.group(1).strip()[:_MAX_FACT_LEN]
    if "investment_budget" not in candidates:
        # A short reply that is only an amount ("Shayad 10 lakh.", "around 5 lakh") answers the budget question
        # the agent just asked; it has no keyword of its own because the question was in the previous turn.
        bare = _BUDGET_RE.search(text)
        if bare and not (set(_tokens_lower(text)) - _BARE_AMOUNT_WORDS - set(_tokens_lower(bare.group(1)))):
            candidates["investment_budget"] = bare.group(1).strip()[:_MAX_FACT_LEN]
    pending_budget = memory.get("investment_budget_pending")
    if pending_budget and "investment_budget" not in candidates:
        # "Haan, 5 lakh hi hai": repeating the guess they gave earlier confirms it, context or not.
        for clause in clauses:
            amount = _BUDGET_RE.search(clause)
            if amount and _norm(amount.group(1)) == _norm(pending_budget):
                candidates["investment_budget"] = amount.group(1).strip()[:_MAX_FACT_LEN]
    for key, new in candidates.items():
        old = memory.get(key)
        pending = memory.get(f"{key}_pending")
        if hedged and not (strong or weak) and not (old and _norm(old) == _norm(new)):
            # "shayad 5 lakh": a guess is kept apart from what the customer has confirmed.
            facts[f"{key}_pending"] = new
            continue
        if not old or _norm(old) == _norm(new):
            facts[key] = new
            if not old and (strong or weak) and pending and _norm(pending) != _norm(new):
                overwritten.add(key)  # "Nahi, actually 7 lakh" to a guess of 10 lakh: the guess is dropped
        else:
            # The customer changed a detail they gave earlier (budget, space): the latest figure simply
            # replaces it. No confirmation round, no "which one is right" question.
            facts[key] = new
            overwritten.add(key)
    if overwritten:
        signals.add(SLOT_UPDATED)
    if (strong and not overwritten) or corrections:
        signals.add(CORRECTION)
    if _DAY_CORRECTION_RE.search(text):
        signals.add(CORRECTION)
        signals.add(DATE_CORRECTION)
    if conflicts:
        signals.add(CONFLICT)

    # --- unavailability / a declined time, and the dates that go with them -------------
    unavailable = bool(_UNAVAILABLE_RE.search(text))
    declined = bool(_DECLINE_TIME_RE.search(text))
    drop_callback = False
    callback_facts: dict[str, str] = {}
    if unavailable or declined:
        if unavailable:
            signals.add(UNAVAILABLE)
        if declined:
            signals.add(DECLINES_TIME)
        drop_callback = True
        unavailable_dates: list[str] = []
        other_clauses: list[str] = []
        for clause in clauses:
            if _UNAVAILABLE_RE.search(clause) or _DECLINE_TIME_RE.search(clause):
                unavailable_dates.extend(_mentioned_dates(clause, today))
            else:
                other_clauses.append(clause)
        facts["unavailable_dates"] = ", ".join(dict.fromkeys(unavailable_dates)) or "unspecified"
        if other_clauses:
            from apps.api.core.conversation.understand import _extract_facts  # lazy: avoids a cycle

            rest = _extract_facts(". ".join(other_clauses), today)
            callback_facts = {k: v for k, v in rest.items() if k.startswith("callback_")}

    return DialogueAnalysis(
        signals=frozenset(signals),
        facts=facts,
        corrections=corrections,
        conflicts=conflicts,
        drop_callback=drop_callback,
        callback_facts=callback_facts,
    )


# --- understanding -----------------------------------------------------------------------------------------------


def _synthesised_scenario(analysis: DialogueAnalysis) -> tuple[str, str] | None:
    """A scenario for a message the base rules did not recognise, when the
    signals say clearly what it is. None when it should go to the model."""
    sig = analysis.signals
    if END_CALL in sig:
        return "closing", "end_call"
    if ASKS_TIME in sig:
        return "objection", "asks_for_time"  # not a question for the team: no handoff, no model call
    if ASKS_IDENTITY in sig:
        return "confused", "asks_identity"
    if REPETITION in sig:
        return "confused", "repetition_complaint"
    if PRESSURE in sig or NO_COMMITMENT in sig or HESITATION in sig or DECLINES_MEETING in sig:
        return "objection", "undecided_or_pressure"
    if analysis.corrections or analysis.conflicts or DATE_CORRECTION in sig:
        return "discovery", "correction"
    if SLOT_UPDATED in sig:
        return "discovery", "slot_update"  # absorbed silently; the base discovery reply carries on
    if UNAVAILABLE in sig or DECLINES_TIME in sig:
        return "busy_callback", "unavailable"
    if PREFERS_ONLINE in sig or PREFERS_IN_PERSON in sig:
        return "interested", "meeting_preference"
    return None


def _protect_verified(
    understanding: Understanding | None, memory: dict[str, str], *, correcting: bool
) -> Understanding | None:
    """Verified facts are not overwritten by one differing mention of a base fact (a name
    mis-heard from a stray "main ..."); only an explicit correction replaces them."""
    if understanding is None or correcting:
        return understanding
    kept = {
        k: v
        for k, v in understanding.facts.items()
        if not (k in _PROTECTED_BASE_FACTS and memory.get(k) and _norm(v) != _norm(memory[k]))
    }
    return understanding if kept == understanding.facts else replace(understanding, facts=kept)


def enhance_understanding(
    understanding: Understanding | None,
    text: str,
    *,
    memory: dict[str, str],
    today: date,
    language: str,
) -> Understanding | None:
    """Attach signals and enhanced facts. ``None`` stays ``None`` (the model
    decides) unless the signals alone make the scenario unambiguous."""
    understanding = _drop_misread_name(understanding)
    analysis = analyse(text, memory=memory, today=today)
    if not analysis.signals and not analysis.facts:
        return _protect_verified(understanding, memory, correcting=False)
    if understanding is None:
        synthesised = _synthesised_scenario(analysis)
        if synthesised is None:
            return None
        scenario, intent = synthesised
        understanding = Understanding(
            scenario=scenario,
            intent=intent,
            emotion="neutral",
            language=language,
            confidence=0.8,
            source="rules",
        )
    facts = dict(understanding.facts)
    if analysis.drop_callback:
        for key in ("callback_date", "callback_time_hhmm", "callback_time"):
            facts.pop(key, None)
        facts.update(analysis.callback_facts)
        if "callback_date" in facts and "callback_time_hhmm" in facts:
            facts["callback_time"] = f"{facts['callback_date']} {facts['callback_time_hhmm']}"
    understanding = replace(understanding, facts=facts)
    if (
        understanding.scenario == "not_interested"
        and DECLINES_MEETING in analysis.signals
        and not _GENERAL_NO_RE.search(text)
    ):
        # "Mujhe meeting nahi chahiye" turns down the meeting, not the product: the lead must not be marked
        # not interested, and the conversation carries on without the meeting.
        understanding = replace(understanding, scenario="objection", intent="declines_meeting")
    understanding = _protect_verified(understanding, memory, correcting=CORRECTION in analysis.signals)
    facts = dict(understanding.facts)
    facts.update(analysis.facts)
    if HESITATION in analysis.signals or PRESSURE in analysis.signals:
        facts["interest_level"] = "undecided"
    return replace(
        understanding,
        facts=facts,
        signals=analysis.signals,
        corrections=dict(analysis.corrections),
        conflicts=dict(analysis.conflicts),
    )


# --- state -------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DialogueState:
    """What happened earlier in this conversation, read back from memory."""

    appointment_asks: int = 0
    answered_topics: frozenset[str] = frozenset()
    handoff_requested: bool = False
    undecided: bool = False
    name_used: bool = False
    meeting_declined: bool = False
    ack_last: bool = False

    @classmethod
    def from_memory(cls, memory: dict[str, str] | None) -> "DialogueState":
        memory = memory or {}
        try:
            asks = int(memory.get("appointment_asks") or 0)
        except ValueError:
            asks = 0
        topics = frozenset(t for t in (memory.get("answered_topics") or "").split(",") if t)
        return cls(
            appointment_asks=max(0, asks),
            answered_topics=topics,
            handoff_requested=bool(memory.get("handoff_requested")),
            undecided=memory.get("interest_level") == "undecided" or bool(memory.get("pressure_concern")),
            name_used=bool(memory.get("name_used")),
            meeting_declined=bool(memory.get("meeting_declined")),
            ack_last=memory.get("ack_last") == "1",
        )


def appointment_ready(understanding: Understanding, state: DialogueState, memory: dict[str, str]) -> bool:
    """May the agent ask the customer to fix a call or meeting? Only when they
    are warm, have not hesitated or voiced a worry about pressure, are not
    unavailable, and have not already been asked once."""
    if understanding.signals & _HEDGE_FLAGS:
        return False
    if state.appointment_asks >= 1 or state.undecided or state.meeting_declined:
        return False
    return (
        understanding.scenario in ("interested", "appointment_request")
        or understanding.facts.get("interest_level") == "high"
        or memory.get("interest_level") == "high"
    )


# --- policy ---------------------------------------------------------------------------------------------------------------

_D_ANSWER_FIRST = (
    "The caller asked a direct question: answer it first, before anything else, then stop. "
    "Do not carry on with your earlier line. Answer accurately and immediately, never ignore it to repeat "
    "an earlier statement or push for a meeting, and only move to a next step once their point is fully "
    "resolved."
)
_D_NO_FIGURES = (
    "You have no approved figure for investment, profit, returns or income, so do not state, "
    "estimate or hint at any number or percentage, and do not say it is good or profitable."
)
_D_NO_PUSH = "Do not ask them to book a meeting, fix a time or take a decision in this reply."
_D_COMMITMENT = (
    "If they ask whether a meeting creates any commitment or obligation, answer only from the "
    "approved facts; if none say so, tell them the first meeting is meant to share information and "
    "the team will confirm any terms. Never promise there is no obligation, and never state fees, "
    "refunds or contract terms."
)
_D_ACK_HESITATION = (
    "Cushion first: they are still deciding, so your reply MUST open with one soft sentence that "
    "acknowledges their doubt and says there is no hurry (for example \"Main aapki uljhan samajh sakti hoon, koi jaldi nahi hai\", in the "
    "caller's language and your own gender). Only after that, gently offer to explain or answer anything. "
    "Do not argue, sell harder, or move straight to a meeting or time."
)
_D_REASSURE = "Briefly reassure them there is no pressure on them to decide."
_D_ONLINE = "They prefer an online meeting: say an online meeting works fine and do not insist on anything else."
_D_FLEXIBLE = (
    "That day or time does not work for them: do not book or schedule anything for it; offer to "
    "talk when it suits them and ask which other day or time is convenient."
)
_D_ALREADY = (
    "They were already given this earlier in the call: reply in one short sentence and ask which "
    "part is unclear. Do not repeat the full explanation."
)
_D_REPETITION = (
    "They feel you are repeating yourself: apologise in a few words, do not repeat anything "
    "already said or ask anything already answered, and carry on from what you already know."
)
_D_BACKGROUND = (
    "They said they have no prior background in this field: acknowledge it without judgement, do "
    "not say whether experience is required unless the approved facts say so, and do not ask about "
    "their background again."
)
_D_SIMPLER = "Explain it differently and more simply, in one or two short sentences; do not repeat your earlier wording."
_D_CLOSE = "Close naturally and warmly. Do not pitch or ask anything new."
_D_MEETING_DECLINED = (
    "They do not want a meeting, or feel pushed toward one: acknowledge that in a few words, say it is no "
    "problem, and do not suggest a meeting, appointment or call again unless they bring it up. Carry on "
    "helping with whatever they ask."
)
_D_DATE_CORRECTION = (
    "They corrected the day or time you suggested. Say sorry once, in a few words, then use exactly the "
    "day or time THEY said and confirm it back in one short sentence. Do not book or schedule anything "
    "until they clearly agree to that confirmed slot."
)
_D_SLOT_UPDATED = (
    "They changed a detail they gave earlier (budget, space or similar). Quietly use the new value from "
    "now on: do not repeat their words or the old value back, do not ask them to confirm it, and do not "
    "say you have updated anything. Carry on with the conversation."
)
_D_NO_HOLD_PHRASE = (
    "Do not use a hold phrase (\"ek second\", \"ek minute\", \"hold on\") and do not react to pauses or "
    "half-finished sentences: reply only to what they have actually finished saying."
)
_D_ACK_PREFIX ="Natural acknowledgement:"
_D_ACK = (
    f"{_D_ACK_PREFIX} at most ONE short opener (\"Ji.\", \"Achha.\", \"Samajh gaya/gayi\", \"Right.\", "
    "\"Okay.\"), only if it fits; many replies need none. Never the same one twice in a row, never to fill a "
    "pause, and add no \"umm\" or other filler."
)
_D_LENGTH = (
    "Match the length to the question: a simple question gets one or two short sentences; a question about "
    "how something works may use up to three short sentences (this replaces the usual one-or-two limit) and "
    "then a check on whether they want the next part. Never talk for long without letting them reply."
)
_SLOT_KEYS = ("investment_budget", "customer_city", "property_info", "business_type", "team_size")
_D_NO_SLOT_ECHO = (
    "CRITICAL: Never repeat, summarize, paraphrase or acknowledge any detail they just gave (budget, number, "
    "city, space, category or industry) back to them in any form. Banned: \"Got it, aapka budget...\", "
    "\"Samajh gaya, aap [category] chahte hain...\", \"Theek hai, [amount]...\", \"ab budget X hai\". Start the "
    "reply directly with the next natural question or step, with NO opener or confirmation word before it. "
    "Update the detail silently and never re-ask for one already given."
)
_D_STYLE = (
    "Keep it short and conversational, one clear question at most, and mix Hindi and English the way "
    "the caller does. Do not repeat explanations or acknowledgements you already gave, and use their "
    "name at most once in the whole call. You are an AI assistant: never claim to be a human employee."
)


def _with(decision: Decision, **changes: Any) -> Decision:
    return replace(decision, **changes)


def _topics_in(understanding: Understanding) -> tuple[str, ...]:
    return tuple(sorted(s.split(":", 1)[1] for s in understanding.signals if s.startswith("topic:")))


def _with_notes(args: dict[str, Any], memory: dict[str, str], facts: dict[str, str]) -> dict[str, Any]:
    """Record the agreed meeting method in a booking's notes (the tool contract is unchanged)."""
    method = facts.get("meeting_preference") or memory.get("meeting_preference")
    if not method or "notes" not in args:
        return args
    return {**args, "notes": f"{args['notes']} | Meeting method: {method}"[:500]}


def enhance_decision(
    decision: Decision,
    understanding: Understanding,
    *,
    text: str,
    memory: dict[str, str],
    knowledge: list[Any],
    state: DialogueState | None = None,
) -> Decision:
    """Apply the dialogue policy to the base engine's decision."""
    state = state or DialogueState.from_memory(memory)
    sig = understanding.signals
    scenario = understanding.scenario
    facts = understanding.facts

    if scenario in _ESCALATION_SCENARIOS:
        if scenario == "closing":
            return _with(decision, directives=(*decision.directives, _D_CLOSE, _D_STYLE), push=False)
        return decision
    # Two kinds of hedge: something that makes a booking premature (hesitation, a pressure
    # worry, a correction or conflict still to settle) and a day/time that does not work
    # (which only blocks scheduling when the customer gave no usable alternative).
    blocking = bool(sig & {HESITATION, PRESSURE, NO_COMMITMENT, CORRECTION, CONFLICT, REPETITION, DECLINES_MEETING})
    # An "alternative" must be a different day from the one they said does not work.
    alternative = bool(
        facts.get("callback_date")
        and facts.get("callback_time_hhmm")
        and facts["callback_date"] not in (facts.get("unavailable_dates") or "")
    )
    schedule_blocked = bool(sig & {UNAVAILABLE, DECLINES_TIME}) and not alternative
    # The customer asked for the booking themselves with nothing hedging it: honour it,
    # as the base engine does (it books only with a stated date AND time).
    if scenario == "appointment_request" and not (blocking or schedule_blocked):
        if decision.action_id == "book_appointment":
            return _with(decision, args=_with_notes(decision.args, memory, facts), directives=(_D_STYLE,))
        return _with(decision, directives=(*decision.directives, _D_STYLE))

    directives: list[str] = list(decision.directives)
    topics = _topics_in(understanding)
    is_question = DIRECT_QUESTION in sig and bool(topics)
    kind = decision.reply_kind
    result = decision

    # 1. Who is this / why are you calling: answered plainly, as an AI.
    if ASKS_IDENTITY in sig:
        result = Decision("none", {}, "identify_self", next_state=STATE_ENGAGED)
        directives.append(
            "They asked who is calling: say briefly who you are (an AI assistant calling for the "
            "business named in your instructions) and why you called, using only what your "
            "instructions say; then ask if now is a good time."
        )
    # 2. A repeated-question complaint is acknowledged and the conversation moves on.
    elif REPETITION in sig:
        result = Decision("none", {}, "repetition_apology", next_state=STATE_ENGAGED)
        directives.append(_D_REPETITION)
    # 3. A direct question comes first, and is never answered with an invented figure.
    elif is_question and (kind in _ANSWERABLE_KINDS) and decision.action_id in ("none", "transfer_to_human"):
        directives.append(_D_ANSWER_FIRST)
        directives.append(_D_LENGTH)
        if knowledge:
            result = Decision("none", {}, "answer", knowledge=list(knowledge), next_state=STATE_ENGAGED)
            if set(topics) <= state.answered_topics:
                directives.append(_D_ALREADY)
        elif state.handoff_requested or set(topics) <= state.answered_topics:
            result = Decision("none", {}, "honest_unknown_repeat", next_state=STATE_ENGAGED)
        else:
            result = Decision(
                "transfer_to_human",
                {"reason": f"Unanswered customer question: {text.strip()[:300]}", "urgency": "low"},
                "unknown_followup",
                next_state=STATE_ENGAGED,
            )
        if not knowledge and FIGURE_TOPICS & set(topics):
            directives.append(_D_NO_FIGURES)
        if "commitment" in topics:
            directives.append(_D_COMMITMENT)
    # 4. Without a question to answer, the customer's situation decides the reply.
    elif sig & _HEDGE_FLAGS or (sig & {PREFERS_ONLINE, PREFERS_IN_PERSON}):
        if CONFLICT in sig and understanding.conflicts:
            key, old = next(iter(understanding.conflicts.items()))
            result = Decision(
                "none",
                {"old": old, "new": facts.get(f"{key}_pending", "")},
                "clarify_conflict",
                next_state=STATE_ENGAGED,
            )
        elif CORRECTION in sig and understanding.corrections:
            key, old = next(iter(understanding.corrections.items()))
            result = Decision(
                "none", {"old": old, "new": facts.get(key, "")}, "confirm_correction", next_state=STATE_ENGAGED
            )
        elif ASKS_TIME in sig and not (sig & {CORRECTION, CONFLICT, DECLINES_MEETING, PRESSURE, NO_COMMITMENT}):
            result = Decision("none", {}, "give_time", next_state=STATE_ENGAGED)
        elif DATE_CORRECTION in sig:
            result = Decision("none", {}, "confirm_day_correction", next_state=STATE_ENGAGED)
        elif DECLINES_MEETING in sig:
            result = Decision("none", {}, "soft_next_step", next_state=STATE_ENGAGED)
        elif PRESSURE in sig or NO_COMMITMENT in sig:
            result = Decision("none", {}, "reassure_no_pressure", next_state=STATE_OBJECTION)
        elif HESITATION in sig:
            result = Decision("none", {}, "hesitation_support", next_state=STATE_OBJECTION)
        elif sig & {UNAVAILABLE, DECLINES_TIME}:
            # With a usable alternative the customer's own proposal stands; otherwise ask for one.
            result = (
                Decision("none", {}, "flexible_followup", next_state=STATE_ENGAGED)
                if schedule_blocked
                else decision
            )
        elif PREFERS_ONLINE in sig:
            result = Decision("none", {}, "online_meeting_ok", next_state=STATE_ENGAGED)
        else:
            result = decision
    elif kind in PUSH_KINDS:
        # A nudge toward a call: only for a customer who is ready, and only once.
        result = decision if appointment_ready(understanding, state, memory) else Decision(
            "none", {}, "soft_next_step", next_state=STATE_ENGAGED
        )
    elif scenario == "confused" and kind == "confused" and state.answered_topics:
        directives.append(_D_SIMPLER)

    # A booking or callback needs a date AND a time the customer gave, and nothing hedging it.
    if result.action_id == "book_appointment":
        if blocking or schedule_blocked:
            result = Decision(
                "none", {}, "flexible_followup" if schedule_blocked and not blocking else "soft_next_step",
                next_state=STATE_ENGAGED,
            )
        elif not (facts.get("callback_date") and facts.get("callback_time_hhmm")):
            result = Decision("none", {}, "ask_appointment_time", next_state=STATE_AWAITING_APPOINTMENT_TIME)
        else:
            result = _with(result, args=_with_notes(result.args, memory, facts))
    if result.action_id == "request_callback":
        if schedule_blocked:
            result = Decision("none", {}, "flexible_followup", next_state=STATE_ENGAGED)
        elif not facts.get("callback_time_hhmm") and " " not in (memory.get("callback_time") or ""):
            result = Decision("none", {}, "ask_callback_time", next_state=STATE_AWAITING_CALLBACK_TIME)
        else:
            result = _with(result, args=_with_notes(result.args, memory, facts))

    # Secondary signals still shape the same reply.
    if CORRECTION in sig and understanding.corrections and result.reply_kind != "confirm_correction":
        directives.append(
            "They corrected something they said earlier: confirm the corrected detail back to them "
            "in one short sentence before continuing, and do not ask for it again."
        )
    if CONFLICT in sig and understanding.conflicts and result.reply_kind != "clarify_conflict":
        directives.append(
            "What they just said conflicts with an earlier detail: ask which one is right before "
            "continuing; do not assume."
        )
    if DECLINES_MEETING in sig:
        directives.append(_D_MEETING_DECLINED)
    if DATE_CORRECTION in sig:
        directives.append(_D_DATE_CORRECTION)
    if PRESSURE in sig or NO_COMMITMENT in sig:
        directives += [_D_REASSURE, _D_COMMITMENT]
    if HESITATION in sig:
        directives.append(_D_ACK_HESITATION)
    if PREFERS_ONLINE in sig:
        directives.append(_D_ONLINE)
    if schedule_blocked:
        directives.append(_D_FLEXIBLE)
    if "customer_background" in facts:
        directives.append(_D_BACKGROUND)
    pushes = result.reply_kind in PUSH_KINDS
    if not pushes and (
        sig & _HEDGE_FLAGS or state.undecided or state.meeting_declined or result.reply_kind == "soft_next_step"
    ):
        directives.append(_D_NO_PUSH)
    if state.meeting_declined and DECLINES_MEETING not in sig:
        directives.append(_D_MEETING_DECLINED)
    if SLOT_UPDATED in sig:
        directives.append(_D_SLOT_UPDATED)
    if sig & {ASKS_TIME, HESITATION}:
        directives.append(_D_NO_HOLD_PHRASE)
    gave_slot = any(facts.get(key) for key in _SLOT_KEYS)
    if gave_slot:
        directives.append(_D_NO_SLOT_ECHO)
    if not gave_slot and not state.ack_last and not (sig & {CORRECTION, CONFLICT}):
        # Acknowledge, but not on every turn: the previous reply's use is remembered (ack_last).
        directives.append(_D_ACK)
    directives.append(_D_STYLE)

    deduped = tuple(dict.fromkeys(directives))
    return _with(result, directives=deduped, push=pushes)


def tool_holds(understanding: Understanding, memory: dict[str, str]) -> dict[str, str]:
    """Tools the model may NOT run on this turn, with the reason (``{}`` when nothing is held).

    The prompt already tells the model not to book on a guess or against a refusal; this is the same rule in a
    form the voice adapter can enforce when the model calls the tool anyway. Pure; only used for orgs on the
    enhanced dialogue."""
    sig, scenario = understanding.signals, understanding.scenario
    holds: dict[str, str] = {}
    declined_now = DECLINES_MEETING in sig
    asked_for_booking = scenario == "appointment_request" and not (sig & {CORRECTION, CONFLICT, DECLINES_MEETING})
    if declined_now or (memory.get("meeting_declined") and not asked_for_booking):
        holds["book_appointment"] = "the caller has said they do not want a meeting"
    elif sig & {CORRECTION, CONFLICT}:
        holds["book_appointment"] = "the caller just corrected a detail: confirm the day and time with them first"
    elif sig & {HESITATION, PRESSURE, NO_COMMITMENT, ASKS_TIME}:
        holds["book_appointment"] = "the caller has not decided: do not book until they clearly agree"
    if scenario != "not_interested" and (declined_now or memory.get("meeting_declined")):
        holds["mark_not_interested"] = "they declined a meeting, not the offer: do not close the lead"
    return holds


def text_tool_holds(text: str, *, meeting_declined: bool, today: date) -> dict[str, str]:
    """``tool_holds`` from the caller's words alone, for a turn whose decision plan did not arrive in time.

    The plan is how the full rule layer reaches the voice adapter, but it can be late; without this the
    guards would silently switch off exactly when the system is slow. Rules only (no model, no I/O), and the
    adapter uses it only for an org it has already seen on the enhanced dialogue."""
    analysis = analyse(text or "", memory={}, today=today)
    understanding = Understanding(
        scenario="unknown_question", intent="", emotion="neutral", language="en", confidence=0.0,
        source="rules", signals=analysis.signals,
    )
    return tool_holds(understanding, {"meeting_declined": "1"} if meeting_declined else {})


# --- memory and CRM outcome -----------------------------------------------------------------------------------------


def memory_updates(
    understanding: Understanding, decision: Decision, state: DialogueState, memory: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """(facts to remember, keys to forget) after this turn: the enhanced facts
    the customer gave, a pending value for an unconfirmed conflict, and the
    call-scoped counters that let the next turn avoid repeating itself."""
    updates: dict[str, str] = {
        k: v for k, v in understanding.facts.items() if k in ENHANCED_MEMORY_KEYS and v
    }
    forget: list[str] = []
    if "unavailable_dates" in updates:
        # Dates accumulate, and a vague "that time doesn't work" never erases a specific day.
        earlier = [d for d in (memory.get("unavailable_dates") or "").split(", ") if d and d != "unspecified"]
        latest = [d for d in updates["unavailable_dates"].split(", ") if d and d != "unspecified"]
        updates["unavailable_dates"] = ", ".join(dict.fromkeys(earlier + latest)) or "unspecified"
    for key in understanding.corrections:  # the pending value is now the confirmed one
        if memory.get(f"{key}_pending"):
            forget.append(f"{key}_pending")
        updates.pop(f"{key}_pending", None)
    for key in _GUARDED_FACTS:  # a confirmed value ends the guess it came from
        if key in updates and memory.get(f"{key}_pending") and f"{key}_pending" not in forget:
            forget.append(f"{key}_pending")
    if decision.reply_kind in PUSH_KINDS:
        updates["appointment_asks"] = str(state.appointment_asks + 1)
    topics = set(_topics_in(understanding))
    if topics and decision.reply_kind in ("answer", "unknown_followup", "honest_unknown_repeat"):
        updates["answered_topics"] = ",".join(sorted(state.answered_topics | topics))[:200]
    if decision.action_id == "transfer_to_human":
        updates["handoff_requested"] = "1"
    if PRESSURE in understanding.signals or NO_COMMITMENT in understanding.signals:
        updates["pressure_concern"] = "1"
    if DECLINES_MEETING in understanding.signals:
        updates["meeting_declined"] = "1"
    updates["ack_last"] = "1" if any(d.startswith(_D_ACK_PREFIX) for d in decision.directives) else "0"
    if decision.reply_kind in _NAMING_KINDS and (memory.get("name") or understanding.facts.get("name")):
        updates["name_used"] = "1"
    return updates, forget


# Lead disposition tags (Lead.tags, an existing JSON list): at most one "disposition:*" at a time.
DISPOSITION_PREFIX = "disposition:"
DISPOSITIONS = (
    "interested", "undecided", "not_interested", "callback_requested", "meeting_proposed", "meeting_confirmed",
)
# A later, weaker signal never replaces a stronger outcome that really happened.
_DISPOSITION_RANK = {
    "interested": 1, "undecided": 1, "meeting_proposed": 2, "callback_requested": 3, "meeting_confirmed": 4,
    "not_interested": 5,
}


def disposition_for(
    understanding: Understanding, decision: Decision, action_result: dict[str, Any] | None = None
) -> str | None:
    """The lead outcome this turn implies, or None to leave the lead alone.

    "meeting_confirmed" and "callback_requested" require the tool to have
    reported success: a proposed time is only ever "meeting_proposed"."""
    ok = action_result is not None and action_result.get("status") == "ok"
    if understanding.intent == "meeting_preference" and decision.action_id == "none":
        return None  # how they want to meet says nothing about whether they have decided
    if decision.action_id == "mark_not_interested" or understanding.scenario == "not_interested":
        return "not_interested"
    if decision.action_id == "book_appointment":
        return "meeting_confirmed" if ok else "meeting_proposed"
    if decision.action_id == "request_callback":
        return "callback_requested" if ok else None
    if decision.reply_kind in ("ask_appointment_time", "ask_callback_time") or decision.reply_kind in PUSH_KINDS:
        return "meeting_proposed"
    if understanding.signals & {HESITATION, PRESSURE, NO_COMMITMENT} or understanding.facts.get("interest_level") == "undecided":
        return "undecided"
    if understanding.scenario in ("interested", "appointment_request"):
        return "interested"
    return None


def merge_disposition(current: str | None, new: str | None) -> str | None:
    """The disposition to store: ``new`` unless ``current`` is a stronger real outcome."""
    if new is None:
        return current
    if current is None:
        return new
    if new in ("interested", "undecided") and current in ("interested", "undecided"):
        return new  # the customer's latest stance wins between the two soft ones
    return new if _DISPOSITION_RANK.get(new, 0) >= _DISPOSITION_RANK.get(current, 0) else current


def apply_disposition_to_tags(tags: list[Any] | None, disposition: str | None) -> list[Any] | None:
    """New tag list with the (merged) disposition, or None if nothing changes."""
    if disposition is None:
        return None
    existing = list(tags or [])
    current = next(
        (str(t)[len(DISPOSITION_PREFIX):] for t in existing if str(t).startswith(DISPOSITION_PREFIX)), None
    )
    merged = merge_disposition(current, disposition)
    if merged == current:
        return None
    kept = [t for t in existing if not str(t).startswith(DISPOSITION_PREFIX)]
    kept.append(f"{DISPOSITION_PREFIX}{merged}")
    return kept
