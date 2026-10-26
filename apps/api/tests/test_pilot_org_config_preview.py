"""The Pilot Org configuration PREVIEW (docs/pilot-org-config.preview.json) must be acceptable to the real admin
schemas and validators, and must honour the pilot's rules, before anyone approves writing it. Nothing here touches
a database."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from apps.api.channels.voice.greeting import has_ai_disclosure, validate_greeting
from apps.api.core.prompts import render_voice_append
from apps.api.core.tools import DISPATCH_TABLE
from apps.api.routers.conversation_engine import KnowledgeIn
from apps.api.schemas.script import ScriptCreateIn

PREVIEW = json.loads((Path(__file__).resolve().parents[3] / "docs" / "pilot-org-config.preview.json").read_text(encoding="utf8"))
SCRIPT = PREVIEW["script"]["body"]
ITEMS = PREVIEW["knowledge"]["items"]


def test_the_script_is_accepted_by_the_real_script_schema() -> None:
    parsed = ScriptCreateIn(**SCRIPT)
    assert parsed.channel == "voice" and parsed.is_default is True and parsed.content.startswith("SYNTHETIC PILOT SCRIPT")


def test_the_greeting_passes_the_real_validator_and_discloses_the_ai() -> None:
    greeting = PREVIEW["greeting"]["body"]["greeting"]
    assert validate_greeting(greeting) == greeting and has_ai_disclosure(greeting) and len(greeting) <= 300
    assert "Veerox" in greeting


def test_every_knowledge_item_is_accepted_by_the_real_schema_and_titles_are_unique() -> None:
    parsed = [KnowledgeIn(**item) for item in ITEMS]
    assert len({p.title for p in parsed}) == len(parsed) and {p.kind for p in parsed} <= {"product", "pricing", "policy", "faq"}


def test_the_preview_is_clearly_marked_fictional_everywhere() -> None:
    assert "FICTIONAL" in PREVIEW["_comment"] and SCRIPT["content"].startswith("SYNTHETIC")
    assert any("Fictional" in i["body"] for i in ITEMS)


def test_no_price_or_result_figure_appears_anywhere() -> None:
    text = json.dumps(PREVIEW, ensure_ascii=False)
    assert not re.search(r"[₹$]|\bRs\.?\b|\blakh\b|\bcrore\b|\d\s?%|per month|per call", text, re.I)
    # The only digits allowed are the fictional demo window and length.
    numbers = set(re.findall(r"\d+(?::\d+)?", SCRIPT["content"]))
    assert numbers <= {"15", "10:00", "18:00"}, numbers
    pricing = next(i for i in ITEMS if i["kind"] == "pricing")
    assert "No prices" in pricing["body"] and not re.search(r"\d", pricing["body"])


def test_the_script_states_every_required_rule() -> None:
    content = SCRIPT["content"].lower()
    for needle in (
        "ai assistant", "never claim to be a human", "only facts you have", "never state, estimate or hint at any price",
        "never invent", "never promise", "wait for a yes", "do not offer a meeting again", "only after the tool reports success",
        "transfer_to_human", "do not say you are connecting them now", "one short, polite goodbye",
    ):
        assert needle in content, needle


def test_the_tools_the_script_names_really_exist() -> None:
    named = set(re.findall(r"\b(send_whatsapp_message|transfer_to_human|request_callback|book_appointment|mark_not_interested)\b", SCRIPT["content"]))
    assert named == {"send_whatsapp_message", "transfer_to_human", "request_callback", "book_appointment", "mark_not_interested"}
    assert named <= set(DISPATCH_TABLE)


def test_the_script_does_not_tell_the_model_to_greet_because_the_greeting_is_separate() -> None:
    assert not re.search(r"\bgreet\b|\bnamaste\b", SCRIPT["content"], re.I)


def test_script_plus_voice_rules_stay_within_a_sane_prompt_size() -> None:
    combined = SCRIPT["content"] + "\n\n" + render_voice_append("Veerox")
    assert len(SCRIPT["content"]) < 4500 and len(combined) < 16_000
    assert "AI assistant" in combined  # the platform rules and the script never contradict the disclosure


def test_nothing_in_the_preview_turns_on_a_new_behaviour() -> None:
    skipped = PREVIEW["not_applied_yet"]
    assert "stays false" in skipped["scenario_config_draft"] and "stays shadow" in skipped["org_voice_mode"]
    assert PREVIEW["env_for_this_pilot_only"] == {"VOICE_BRAND_NAME": "Veerox"}


@pytest.mark.parametrize("field", ["name", "content"])
def test_the_script_needs_its_required_fields(field: str) -> None:
    broken = {k: v for k, v in SCRIPT.items() if k != field}
    with pytest.raises(Exception):
        ScriptCreateIn(**broken)
