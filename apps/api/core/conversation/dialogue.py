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
)

# Facts that, once given, are only replaced when the customer clearly corrects
# them (or states the same new value twice), never by one differing mention.
_GUARDED_FACTS = ("property_info", "investment_budget")
# Base-engine facts protected the same way.
_PROTECTED_BASE_FACTS = ("name", "business_type", "team_size")

_HEDGE_FLAGS = frozenset(
    {HESITATION, PRESSURE, NO_COMMITMENT, UNAVAILABLE, DECLINES_TIME, CORRECTION, CONFLICT, REPETITION}
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
    "repetition_apology", "identify_self",
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


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text) if c and c.strip()]


def detect_topics(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _TOPIC_PATTERNS.items() if pattern.search(text))


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

    if _HESITATION_RE.search(text):
        signals.add(HESITATION)
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
    for key, new in candidates.items():
        old = memory.get(key)
        pending = memory.get(f"{key}_pending")
        if not old or _norm(old) == _norm(new):
            facts[key] = new
        elif strong or weak or (pending and _norm(pending) == _norm(new)):
            facts[key] = new
            corrections[key] = old
        else:
            conflicts[key] = old
            facts[f"{key}_pending"] = new
    if strong or corrections:
        signals.add(CORRECTION)
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
    if ASKS_IDENTITY in sig:
        return "confused", "asks_identity"
    if REPETITION in sig:
        return "confused", "repetition_complaint"
    if PRESSURE in sig or NO_COMMITMENT in sig or HESITATION in sig:
        return "objection", "undecided_or_pressure"
    if analysis.corrections or analysis.conflicts:
        return "discovery", "correction"
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
        )


def appointment_ready(understanding: Understanding, state: DialogueState, memory: dict[str, str]) -> bool:
    """May the agent ask the customer to fix a call or meeting? Only when they
    are warm, have not hesitated or voiced a worry about pressure, are not
    unavailable, and have not already been asked once."""
    if understanding.signals & _HEDGE_FLAGS:
        return False
    if state.appointment_asks >= 1 or state.undecided:
        return False
    return (
        understanding.scenario in ("interested", "appointment_request")
        or understanding.facts.get("interest_level") == "high"
        or memory.get("interest_level") == "high"
    )


# --- policy ---------------------------------------------------------------------------------------------------------------

_D_ANSWER_FIRST = (
    "The caller asked a direct question: answer it first, before anything else, then stop. "
    "Do not carry on with your earlier line."
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
    "Acknowledge briefly that they are still deciding and that there is no hurry; do not argue "
    "or sell harder."
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
    blocking = bool(sig & {HESITATION, PRESSURE, NO_COMMITMENT, CORRECTION, CONFLICT, REPETITION})
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
    if not pushes and (sig & _HEDGE_FLAGS or state.undecided or result.reply_kind == "soft_next_step"):
        directives.append(_D_NO_PUSH)
    directives.append(_D_STYLE)

    deduped = tuple(dict.fromkeys(directives))
    return _with(result, directives=deduped, push=pushes)


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
    if decision.reply_kind in PUSH_KINDS:
        updates["appointment_asks"] = str(state.appointment_asks + 1)
    topics = set(_topics_in(understanding))
    if topics and decision.reply_kind in ("answer", "unknown_followup", "honest_unknown_repeat"):
        updates["answered_topics"] = ",".join(sorted(state.answered_topics | topics))[:200]
    if decision.action_id == "transfer_to_human":
        updates["handoff_requested"] = "1"
    if PRESSURE in understanding.signals or NO_COMMITMENT in understanding.signals:
        updates["pressure_concern"] = "1"
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
