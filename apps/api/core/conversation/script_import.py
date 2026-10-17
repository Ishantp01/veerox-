"""Turn an existing script into structured suggestions (blueprint section 13).

"The system should help transform the script into structured sections without
deleting the original source." This module only ever *reads* a script and
returns suggestions; nothing is saved here. The admin reviews them, then adds the
ones they want to the draft (scenarios) or to approved knowledge.

Two layers:

* ``split_sections`` - deterministic, no model: splits the script on its own
  headings / numbered lines into labelled sections.
* ``suggest_from_script`` - one model call that proposes approved-knowledge items
  and scenarios. Anything it proposes is checked: a fact that contains a number
  (a price, a day count, a percentage) which does not appear in the script is
  dropped, so the model cannot invent a figure into the approved knowledge.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from apps.api.core.conversation.config import SCENARIO_ID_RE
from apps.api.core.conversation.understand import LLMCall

logger = structlog.get_logger(__name__)

MAX_SCRIPT_CHARS = 20_000
MAX_KNOWLEDGE = 15
MAX_SCENARIOS = 10
KNOWLEDGE_KINDS = ("product", "pricing", "policy", "faq")

_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s+.+|\d{1,2}[.)]\s+.{2,80}|[A-Z][A-Za-z0-9 &/'-]{2,60}:\s*|[A-Z][A-Z0-9 &/'-]{3,60})\s*$"
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Words that, in a heading, say which part of a sales script it is.
_SECTION_HINTS = (
    ("opening", ("opening", "greeting", "introduction", "intro", "start")),
    ("product", ("product", "feature", "service", "offer", "benefit", "about")),
    ("pricing", ("price", "pricing", "cost", "plan", "fee", "charges")),
    ("qualification", ("qualif", "question", "discovery", "requirement")),
    ("objections", ("objection", "concern", "faq", "doubt")),
    ("compliance", ("compliance", "disclaimer", "legal", "policy", "terms", "do not", "don't")),
    ("closing", ("closing", "close", "cta", "next step", "goal", "end")),
)


@dataclass
class Section:
    title: str
    kind: str
    text: str

    def to_json(self) -> dict[str, str]:
        return {"title": self.title, "kind": self.kind, "text": self.text}


@dataclass
class Suggestions:
    sections: list[Section] = field(default_factory=list)
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    model_used: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "sections": [s.to_json() for s in self.sections],
            "knowledge": self.knowledge,
            "scenarios": self.scenarios,
            "dropped": self.dropped,
            "model_used": self.model_used,
        }


def _kind_for(title: str) -> str:
    lowered = title.lower()
    for kind, hints in _SECTION_HINTS:
        if any(hint in lowered for hint in hints):
            return kind
    return "other"


def split_sections(text: str) -> list[Section]:
    """Split a script into labelled sections using its own headings. A script
    with no headings becomes one "script" section per blank-line paragraph group."""
    lines = text.replace("\r\n", "\n").split("\n")
    sections: list[Section] = []
    title = "Script"
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(Section(title.strip(" #:.)"), _kind_for(title), body[:3000]))
        buffer.clear()

    has_heading = any(_HEADING_RE.match(line) and len(line.strip()) < 70 for line in lines if line.strip())
    for line in lines:
        if has_heading and line.strip() and _HEADING_RE.match(line) and len(line.strip()) < 70:
            flush()
            title = line.strip()
        elif not has_heading and not line.strip() and buffer:
            flush()
            title = f"Part {len(sections) + 1}"
        else:
            buffer.append(line)
    flush()
    return sections[:40]


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    slug = re.sub(r"^[^a-z]+", "", slug)[:28]
    return slug


def _unique_id(name: str, taken: set[str]) -> str | None:
    base = _slug(name)
    if not base or len(base) < 2:
        return None
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"_{n}"
        candidate = base[: 30 - len(suffix)] + suffix
        n += 1
    return candidate if SCENARIO_ID_RE.match(candidate) else None


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text)}


_SYSTEM = (
    "You help a business turn its sales/support script into structured setup for an AI agent. "
    "Read the script and reply with ONE JSON object and nothing else, with keys:\n"
    '"knowledge": a list of approved facts the AI may state, each {"kind": one of '
    '"product","pricing","policy","faq", "title", "body", "keywords" (comma separated words '
    "a customer might use, include Hindi/Hinglish spellings)};\n"
    '"scenarios": a list of situations worth their own handling, each {"name", "situation", '
    '"examples" (3 to 6 short things a customer might say), "goal", "should_do", "must_avoid"}.\n'
    "Rules: copy facts, prices, durations and policies EXACTLY as written in the script. Never "
    "add a number, price, date or promise that is not in the script. If something is unclear, "
    "leave it out. Do not suggest scenarios that already exist as basic ones (greeting, price "
    "question, busy, not interested, human handoff, closing). Maximum 15 knowledge items and "
    "10 scenarios."
)


async def suggest_from_script(
    text: str,
    *,
    existing_scenario_ids: set[str],
    llm: LLMCall | None,
    timeout: float = 45.0,
) -> Suggestions:
    """Sections always; knowledge and scenario suggestions when a model is available."""
    text = text.strip()[:MAX_SCRIPT_CHARS]
    result = Suggestions(sections=split_sections(text))
    if not text or llm is None:
        return result

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Script:\n\n{text}"},
    ]
    try:
        reply = await asyncio.wait_for(llm(messages), timeout=timeout)
        raw = (reply.content or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except Exception as exc:  # noqa: BLE001 - the deterministic sections are still useful
        logger.warning("script_suggestion_failed", error=type(exc).__name__)
        result.dropped.append(
            f"The AI model could not produce suggestions ({type(exc).__name__}); only the sections are shown."
        )
        return result
    result.model_used = True

    script_numbers = _numbers(text)
    seen_titles: set[str] = set()
    for item in (data.get("knowledge") or [])[: MAX_KNOWLEDGE * 2]:
        if not isinstance(item, dict) or len(result.knowledge) >= MAX_KNOWLEDGE:
            continue
        title = str(item.get("title") or "").strip()[:200]
        body = str(item.get("body") or "").strip()[:1500]
        if not title or not body or title.lower() in seen_titles:
            continue
        invented = _numbers(body) - script_numbers
        if invented:
            result.dropped.append(
                f'Left out "{title}": it contains {", ".join(sorted(invented))}, which is not in your script.'
            )
            continue
        seen_titles.add(title.lower())
        kind = str(item.get("kind") or "faq").lower()
        result.knowledge.append(
            {
                "kind": kind if kind in KNOWLEDGE_KINDS else "faq",
                "title": title,
                "body": body,
                "keywords": str(item.get("keywords") or "").strip()[:300] or None,
            }
        )

    taken = set(existing_scenario_ids)
    for item in (data.get("scenarios") or [])[: MAX_SCENARIOS * 2]:
        if not isinstance(item, dict) or len(result.scenarios) >= MAX_SCENARIOS:
            continue
        name = str(item.get("name") or "").strip()[:80]
        goal = str(item.get("goal") or "").strip()[:500]
        raw_examples = item.get("examples") if isinstance(item.get("examples"), list) else []
        examples = [str(e).strip()[:120] for e in raw_examples if str(e).strip()][:8]
        scenario_id = _unique_id(name, taken) if name else None
        if not scenario_id or not goal or not examples:
            continue
        should_do = str(item.get("should_do") or "").strip()[:800]
        invented = _numbers(should_do) - script_numbers
        if invented:
            result.dropped.append(
                f'Left out scenario "{name}": its instructions contain {", ".join(sorted(invented))}, '
                "which is not in your script."
            )
            continue
        taken.add(scenario_id)
        result.scenarios.append(
            {
                "id": scenario_id,
                "name": name,
                "situation": str(item.get("situation") or "").strip()[:500],
                "examples": examples,
                "goal": goal,
                "should_do": should_do,
                "must_avoid": str(item.get("must_avoid") or "").strip()[:800],
                "actions": ["none"],
                "next_scenarios": [],
                "enabled": True,
                "reply_example": "",
            }
        )
    return result
