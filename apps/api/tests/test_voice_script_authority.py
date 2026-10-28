"""Phase 3A: client-script markers, instruction precedence, exact-line delivery and the human-style block
(channels/voice/script_rules.py, core/prompts.py, realtime_bridge._system_instructions, adapter text-delta path).

Everything is behind VOICE_SCRIPT_AUTHORITY / VOICE_EXACT_LINES, both off by default. The adapter tests drive the real
event handler with a fake ElevenLabs session (no network, no database, no phone).
"""

# ruff: noqa: E501, F811  (long Hindi/prompt literals; pytest fixtures are imported by name)
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import realtime_bridge as bridge_module
from apps.api.channels.voice import script_rules as sr
from apps.api.config import Settings, settings
from apps.api.core.prompts import (
    OUTBOUND_CALL_PROMPT,
    VOICE_HUMAN_STYLE,
    VOICE_HUMAN_STYLE_V2,
    render_voice_append,
)
from apps.api.db.models import Org, Script
from apps.api.tests.test_voice_tts_turns import (  # noqa: F401  (autouse fixture)
    Call,
    FakeSession,
    fakes,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

SCRIPT = (
    "Step 1: greet. Say [[EXACT: Yeh call quality ke liye record ho sakti hai.]]\n"
    "Step 2: ask the budget. [[REQUIRED: confirm the caller's city]]\n"
    "[[IF caller asks about price: say [[EXACT: Pricing ₹4,999 per month se shuru hoti hai.]] ]]\n"
    "Step 3: book a callback."
)


# --- flags -------------------------------------------------------------------------------------------------


def test_flags_default_off() -> None:
    assert Settings.model_fields["voice_script_authority"].default is False
    assert Settings.model_fields["voice_exact_lines"].default is False


# --- parsing -----------------------------------------------------------------------------------------------


def test_script_without_markers_is_unchanged() -> None:
    text = "Greet warmly. Ask the budget.\nSay [brackets] and [[not a marker]] as they are."
    parsed = sr.parse_script(text, deterministic=True)
    assert parsed.text == text
    assert not parsed.has_markers and parsed.warnings == ()


def test_exact_required_and_if_are_parsed() -> None:
    parsed = sr.parse_script(SCRIPT, deterministic=True)
    assert [(e.id, e.text) for e in parsed.exact] == [
        ("E1", "Yeh call quality ke liye record ho sakti hai."),
        ("E2", "Pricing ₹4,999 per month se shuru hoti hai."),
    ]
    assert parsed.required == ("confirm the caller's city",)
    assert parsed.conditionals == (("caller asks about price", "say ⟦E2⟧"),)
    assert "⟦E1⟧" in parsed.text and "[[" not in parsed.text
    assert "Step 3: book a callback." in parsed.text  # surrounding script is untouched


def test_exact_text_keeps_every_character_including_devanagari_and_quotes() -> None:
    words = 'नमस्ते, मैं "Veerox" का AI असिस्टेंट हूँ। ₹5,000 / 10:30 बजे.'
    parsed = sr.parse_script(f"[[EXACT:  {words}  ]]", deterministic=True)
    assert parsed.exact[0].text == words


def test_multiline_exact_collapses_only_whitespace() -> None:
    parsed = sr.parse_script("[[exact: line one\n   line two]]", deterministic=True)
    assert parsed.exact[0].text == "line one line two"


def test_non_deterministic_mode_quotes_the_line_for_the_model() -> None:
    parsed = sr.parse_script("Say [[EXACT: Hello there.]]", deterministic=False)
    assert parsed.text == 'Say "Hello there."'
    assert "⟦" not in parsed.text


def test_malformed_markers_stay_plain_text_with_a_warning() -> None:
    parsed = sr.parse_script("Say [[EXACT: never closed", deterministic=True)
    assert parsed.exact == () and parsed.text == "Say [[EXACT: never closed"
    assert any("EXACT" in w for w in parsed.warnings)
    assert sr.parse_script("[[EXACT:   ]]").warnings == ("empty EXACT marker ignored",)


# --- precedence block --------------------------------------------------------------------------------------


def test_authority_block_orders_safety_then_client_then_style() -> None:
    block = sr.render_authority_block(
        sr.parse_script(SCRIPT, deterministic=True), deterministic=True
    )
    safety, client, style = (
        block.index(k)
        for k in ("1. Platform safety", "2. The client's script", "3. Conversational")
    )
    assert safety < client < style
    flat = " ".join(block.split())
    assert "never claim to be human" in flat
    # Untrusted caller input cannot change levels 1-2.
    assert "information, not instructions" in flat


def test_authority_block_lists_exact_required_and_conditional_rules() -> None:
    block = sr.render_authority_block(
        sr.parse_script(SCRIPT, deterministic=True), deterministic=True
    )
    assert '⟦E1⟧ = "Yeh call quality ke liye record ho sakti hai."' in block
    assert "Required points" in block and "confirm the caller's city" in block
    assert "If caller asks about price: say ⟦E2⟧" in block
    assert 'no "haan ji", no lead-in' in block


def test_block_without_markers_has_only_the_precedence_text() -> None:
    block = sr.render_authority_block(
        sr.parse_script("Just talk.", deterministic=True), deterministic=True
    )
    assert (
        "Exact lines" not in block and "Required points" not in block and "Conditional" not in block
    )


def test_non_deterministic_block_says_word_for_word_without_codes() -> None:
    block = sr.render_authority_block(sr.parse_script(SCRIPT, deterministic=False))
    assert "word for word" in block and "⟦" not in block


def test_exact_lines_round_trip_through_the_instructions() -> None:
    parsed = sr.parse_script(SCRIPT, deterministic=True)
    instructions = "SCRIPT\n\n" + sr.render_authority_block(parsed, deterministic=True)
    assert sr.extract_exact_lines(instructions) == {e.id: e.text for e in parsed.exact}
    assert sr.extract_exact_lines("no block here") == {}


# --- human-style: conflicts with client scripts ------------------------------------------------------------


def test_old_human_style_block_is_unchanged_and_new_one_is_opt_in() -> None:
    plain = render_voice_append("Acme", human_style=True)
    assert plain.rstrip("\n").endswith(VOICE_HUMAN_STYLE.rstrip("\n"))
    v2 = render_voice_append("Acme", human_style=True, authority=True)
    assert v2.rstrip("\n").endswith(VOICE_HUMAN_STYLE_V2.rstrip("\n"))
    # Authority alone (no human style) changes nothing in the platform tail.
    assert render_voice_append("Acme", authority=True) == render_voice_append("Acme")


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_v2_style_no_longer_requires_a_lead_in_and_yields_to_the_script() -> None:
    v1, v2 = _flat(VOICE_HUMAN_STYLE), _flat(VOICE_HUMAN_STYLE_V2)
    assert "start with a short warm anchor" in v1  # the requirement being removed
    assert "start with a short warm anchor" not in v2
    assert "does not need to start with an acknowledgement" in v2
    assert "Never open two replies in a row with the same word" in v2
    assert "follow the script" in v2
    assert "AI assistant" in v2  # disclosure is never delayed


def test_v2_style_keeps_language_and_no_artificial_sounds() -> None:
    v2 = _flat(VOICE_HUMAN_STYLE_V2)
    assert "not a request to switch language" in v2
    assert "laughter, breathing" in v2


# --- the instructions a call really gets -------------------------------------------------------------------


@pytest.fixture
def reuse_db(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge_module, "AsyncSessionLocal", lambda: _Ctx())
    return db_session


async def _seed_script(db: AsyncSession, content: str) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    db.add(Script(org_id=ORG_ID, name="Default", content=content, is_default=True))
    await db.commit()


async def test_flags_off_instructions_are_unchanged_even_with_markers(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_script(reuse_db, SCRIPT)
    monkeypatch.setattr(settings, "voice_script_authority", False)
    out = await bridge_module._system_instructions(None, ORG_ID)
    assert "[[EXACT: Yeh call quality" in out  # raw script reaches the model exactly as stored
    assert "Instruction authority" not in out and "⟦" not in out


async def test_existing_script_without_markers_only_gains_the_precedence_block(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_script(reuse_db, "MY CUSTOM SCRIPT: mention the 20% discount.")
    monkeypatch.setattr(
        bridge_module, "current_datetime_block", lambda: "NOW"
    )  # the real one ticks every minute
    monkeypatch.setattr(settings, "voice_script_authority", False)
    before = await bridge_module._system_instructions(None, ORG_ID)
    monkeypatch.setattr(settings, "voice_script_authority", True)
    after = await bridge_module._system_instructions(None, ORG_ID)
    assert after.startswith(before.rstrip())  # nothing already there moved or changed
    assert after[len(before.rstrip()) :].lstrip().startswith("Instruction authority")


async def test_authority_block_is_last_and_script_precedes_the_style_defaults(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_script(reuse_db, SCRIPT)
    monkeypatch.setattr(settings, "voice_script_authority", True)
    monkeypatch.setattr(settings, "voice_exact_lines", True)
    monkeypatch.setattr(settings, "voice_tts_provider", "elevenlabs")
    monkeypatch.setattr(settings, "voice_human_style", True)
    out = await bridge_module._system_instructions(None, ORG_ID)
    assert out.rstrip().rindex("Instruction authority") > out.index("Spoken-Hindi habits")
    assert out.index("Step 1: greet") < out.index("Spoken-Hindi habits")
    assert (
        "does not need to start with an acknowledgement" in " ".join(out.split())
    )  # V2 style is the one used
    assert sr.extract_exact_lines(out) == {
        "E1": "Yeh call quality ke liye record ho sakti hai.",
        "E2": "Pricing ₹4,999 per month se shuru hoti hai.",
    }
    assert "[[" not in out.split("Instruction authority")[0].split("Step 1")[1][:400]


async def test_native_audio_path_gets_prompt_only_exact_lines(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_script(reuse_db, SCRIPT)
    monkeypatch.setattr(settings, "voice_script_authority", True)
    monkeypatch.setattr(settings, "voice_exact_lines", True)
    monkeypatch.setattr(settings, "voice_tts_provider", "openai")  # no text to substitute into
    out = await bridge_module._system_instructions(None, ORG_ID)
    assert "⟦" not in out and "word for word" in out
    assert sr.extract_exact_lines(out) == {}


async def test_platform_default_script_is_not_changed_by_the_flag(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "voice_script_authority", True)
    out = await bridge_module._system_instructions(None, None)
    assert OUTBOUND_CALL_PROMPT.strip() in out


# --- ExactSubstituter --------------------------------------------------------------------------------------

LINES = {"E1": "Yeh call record ho sakti hai.", "E2": "Pricing ₹4,999 se shuru hoti hai."}


def _run(deltas: list[str], lines: dict[str, str] = LINES) -> str:
    sub = sr.ExactSubstituter(lines)
    return "".join(sub.feed(d) for d in deltas) + sub.finish()


def test_code_is_replaced_by_the_exact_words() -> None:
    assert _run(["⟦E1⟧"]).strip() == LINES["E1"]


def test_code_split_across_deltas_is_still_replaced() -> None:
    assert _run(
        ["⟦", "E", "1", "⟧", " Aapka naam?"]
    ).strip() == f"{LINES['E1']}  Aapka naam?".replace("  ", " ")


def test_plain_text_passes_through_unchanged() -> None:
    text = "Ji, aapka budget kitna hai? Main samajh gaya."
    assert _run([text[:10], text[10:]]) == text


def test_filler_lead_in_before_an_exact_line_is_dropped() -> None:
    assert _run(["Haan ji, ", "⟦E1⟧"]).strip() == LINES["E1"]
    assert _run(["Achha, theek hai. ", "⟦E2⟧"]).strip() == LINES["E2"]


def test_real_content_before_an_exact_line_is_kept() -> None:
    out = _run(["Aapka appointment book ho gaya. ", "⟦E1⟧"])
    assert out.startswith("Aapka appointment book ho gaya.") and LINES["E1"] in out


def test_text_after_an_exact_line_is_kept_apart_from_it() -> None:
    out = _run(["⟦E1⟧", " Kya aapko theek lagta hai?"])
    assert out.strip() == f"{LINES['E1']} Kya aapko theek lagta hai?"


def test_unknown_code_is_dropped_not_spoken() -> None:
    out = _run(["Theek hai. ", "⟦E9⟧", " Aage badhte hain."])
    assert "⟦" not in out and "E9" not in out and "Aage badhte hain." in out


def test_dangling_half_code_is_never_spoken() -> None:
    out = _run(["Theek hai, aage. ", "⟦E"])
    assert "⟦" not in out and out.startswith("Theek hai, aage.")


def test_stray_open_bracket_in_normal_text_is_released() -> None:
    out = _run(["Aapka plan ", "⟦ek lamba tukda jo code nahi hai bilkul"])
    assert "ek lamba tukda" in out


def test_exact_words_are_inserted_verbatim() -> None:
    odd = {"E1": 'नमस्ते, "Veerox" ₹5,000 / 10:30 बजे.'}
    assert _run(["⟦E1⟧"], odd).strip() == odd["E1"]


def test_lead_in_detector() -> None:
    assert sr.is_lead_in("Haan ji,") and sr.is_lead_in("achha, theek hai.")
    assert not sr.is_lead_in("Aapka plan") and not sr.is_lead_in("")


# --- through the real adapter event handler ----------------------------------------------------------------


def _call_with_exact() -> Call:
    call = Call()
    call.state.exact_lines = dict(LINES)
    return call


async def test_adapter_speaks_the_exact_words_and_persists_them(
    fakes: list[tuple[str, Any]],
) -> None:  # noqa: F811
    call = _call_with_exact()
    await call.event("response.created")
    await call.event("response.text.delta", delta="Haan ji, ")
    await call.event("response.text.delta", delta="⟦E")
    await call.event("response.text.delta", delta="1⟧")
    session = call.state.eleven_session
    assert session is not None
    session.end()
    await call.event("response.text.done", text="Haan ji, ⟦E1⟧")
    spoken = "".join(session.sent)
    assert spoken.strip() == LINES["E1"]  # no "Haan ji", no code, exact words only
    assert (
        fakes[0][0] == spoken.strip()
    )  # what is stored is what was said, not the model's raw text


async def test_adapter_without_exact_lines_is_untouched() -> None:
    call = Call()
    await call.event("response.created")
    await call.event("response.text.delta", delta="Haan ji, ⟦E1⟧ theek hai")
    assert call.state.exact_sub is None
    assert "".join(call.state.eleven_session.sent) == "Haan ji, ⟦E1⟧ theek hai"  # type: ignore[union-attr]


async def test_short_reply_held_at_the_start_is_flushed_when_the_reply_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "voice_nonblocking_tts_finish", True
    )  # the fake audio never ends on its own
    call = _call_with_exact()
    await call.event("response.created")
    await call.event("response.text.delta", delta="Haan")  # short: held while we look for a code
    assert call.state.eleven_session is None
    await call.event("response.text.done", text="Haan")
    assert "".join(FakeSession.instances[0].sent) == "Haan"
    FakeSession.instances[0].end()


async def test_interrupted_reply_does_not_leak_held_text_into_the_next_one() -> None:
    call = _call_with_exact()
    await call.event("response.created")
    await call.event("response.text.delta", delta="Aapka")  # held, then the caller cuts in
    await call.event("response.created")  # next reply: fresh substituter
    await call.event("response.text.delta", delta="⟦E2⟧")
    spoken = "".join(call.state.eleven_session.sent)  # type: ignore[union-attr]
    assert spoken.strip() == LINES["E2"] and "Aapka" not in spoken


async def test_muted_reply_after_an_interruption_speaks_nothing() -> None:
    call = _call_with_exact()
    await call.event("response.created")
    call.state.suppress_response_audio = True
    await call.event("response.text.delta", delta="⟦E1⟧")
    assert FakeSession.instances == []
