"""Tests for channels/voice/greeting.py and the pieces around it: greeting
text validation, the opening instruction (and its backward-compatible legacy
form), brand consistency in the voice prompt, and the opt-in audio cache.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import pytest

from apps.api.channels.voice import adapter, greeting
from apps.api.channels.voice.greeting import (
    GreetingAudioCache,
    GreetingError,
    greeting_instruction,
    has_ai_disclosure,
    validate_greeting,
)
from apps.api.config import settings
from apps.api.core.prompts import VOICE_APPEND, render_voice_append

ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


# --- validation -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Namaste, main Acme ka AI assistant hoon.",
        "Hello, this is Acme's virtual assistant.",
        "नमस्ते, मैं एक एआई असिस्टेंट बोल रहा हूं",
        "Hi, you are speaking with an automated assistant from Acme.",
    ],
)
def test_disclosure_is_recognised(text: str) -> None:
    assert has_ai_disclosure(text)


def test_hinglish_words_are_not_mistaken_for_ai() -> None:
    # Case-sensitive on purpose: "hai"/"ai" inside ordinary words must not count.
    assert not has_ai_disclosure("Namaste, Acme se baat ho rahi hai, kaise hain aap")


def test_validate_normalises_and_enforces_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_ai_disclosure", True)
    assert validate_greeting("  Namaste,   main Acme ka AI assistant hoon.  ") == (
        "Namaste, main Acme ka AI assistant hoon."
    )
    with pytest.raises(GreetingError, match="AI assistant"):
        validate_greeting("Namaste, Acme se baat kar rahe hain")


def test_validate_without_disclosure_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_ai_disclosure", False)
    assert validate_greeting("Namaste, Acme se baat kar rahe hain") == "Namaste, Acme se baat kar rahe hain"


def test_blank_clears_and_bad_text_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_ai_disclosure", True)
    assert validate_greeting(None) is None
    assert validate_greeting("   \n ") is None
    with pytest.raises(GreetingError, match="too long"):
        validate_greeting("AI " + "x" * greeting.MAX_GREETING_CHARS)
    with pytest.raises(GreetingError, match="control"):
        validate_greeting("AI assistant \x07 hello")


# --- instruction -----------------------------------------------------------------------------------

# The exact first-response instruction realtime_bridge.py sent before this
# change. With disclosure switched off and the brand it used, behaviour must be
# byte-for-byte what existing customers have today.
_LEGACY_INSTRUCTION = (
    "Begin the call now: open with a short, warm, natural "
    "greeting in Hindi - something like 'Namaste, "
    "WorkAssign AI se Veer bol raha hoon. Main aapki kya "
    "madad kar sakta hoon?' - adapted to match your "
    "persona and script. Do not ask which language they "
    "want - just start the conversation in Hindi."
)


def test_legacy_instruction_is_unchanged_when_disclosure_is_off() -> None:
    assert greeting_instruction(None, "WorkAssign AI", ai_disclosure=False) == _LEGACY_INSTRUCTION


def test_default_instruction_discloses_the_ai_and_uses_the_configured_brand() -> None:
    text = greeting_instruction(None, "Acme", ai_disclosure=True)
    assert "Acme ka AI assistant" in text
    assert "must make clear that you are an AI assistant" in text
    assert "Do not ask which language they want" in text


def test_a_custom_script_supplies_the_opening_not_the_generic_example() -> None:
    text = greeting_instruction(None, "WorkAssign AI", True, from_script=True)
    assert "Veer" not in text and "something like" not in text
    assert "your script" in text and "AI assistant" in text
    with_notice = greeting_instruction(None, "WorkAssign AI", True, notice="This call may be recorded.", from_script=True)
    assert "This call may be recorded." in with_notice
    assert "AI assistant" not in greeting_instruction(None, "WorkAssign AI", False, from_script=True)


def test_without_a_custom_script_or_with_a_fixed_greeting_nothing_changes() -> None:
    assert greeting_instruction(None, "WorkAssign AI", False, from_script=False) == _LEGACY_INSTRUCTION
    fixed = "Namaste, main Acme ka AI assistant hoon."
    assert greeting_instruction(fixed, "x", True, from_script=True) == greeting_instruction(fixed, "x", True)


def test_the_prompt_tail_defers_to_a_custom_scripts_opening() -> None:
    from apps.api.core.prompts import VOICE_APPEND, render_voice_append

    assert render_voice_append("WorkAssign AI") == VOICE_APPEND  # default unchanged
    assert "Veer bol raha hoon" in VOICE_APPEND
    for disclosure in (True, False):
        tail = render_voice_append("WorkAssign AI", ai_disclosure=disclosure, from_script=True)
        assert "Veer" not in tail and "something like" not in tail.split("Language:")[1][:300]
        assert "exactly as your script says to start it" in tail and "ONLY that opening" in tail


def test_the_greeting_turn_carries_the_script_it_refers_to() -> None:
    from apps.api.channels.voice import realtime_bridge as bridge

    session = "SANA SCRIPT BODY\n\nVOICE TAIL"
    opening = greeting_instruction(None, "WorkAssign AI", True, from_script=True)
    assert bridge._greeting_response_instructions(session, opening, None).startswith("SANA SCRIPT BODY")
    # fixed org greeting and the built-in prompt are self-contained: unchanged
    assert bridge._greeting_response_instructions(session, "FIXED", "Namaste") == "FIXED"
    from apps.api.core.prompts import OUTBOUND_CALL_PROMPT

    assert bridge._greeting_response_instructions(OUTBOUND_CALL_PROMPT.strip() + "\n\nx", "G", None) == "G"


async def test_the_bridge_tells_a_custom_script_from_the_built_in_prompt() -> None:
    from apps.api.channels.voice import realtime_bridge as bridge
    from apps.api.core.prompts import OUTBOUND_CALL_PROMPT

    assert bridge._runs_a_custom_script("Sana ki script...\n\nrest") is True
    assert bridge._runs_a_custom_script(OUTBOUND_CALL_PROMPT.strip() + "\n\ntail") is False


def test_org_greeting_is_spoken_verbatim_with_nothing_added() -> None:
    text = greeting_instruction("Namaste, main Acme ka AI assistant hoon.", "Ignored", ai_disclosure=True)
    assert "exactly this greeting, word for word" in text
    assert "'Namaste, main Acme ka AI assistant hoon.'" in text
    assert "Ignored" not in text


# --- brand consistency in the voice prompt --------------------------------------------------------------


def test_voice_prompt_no_longer_names_a_different_brand_than_the_greeting() -> None:
    rendered = render_voice_append("Acme", ai_disclosure=True)
    assert "Acme ka AI assistant" in rendered
    assert "Veerox se baat" not in rendered and "__VOICE_GREETING_EXAMPLE__" not in rendered
    # Disclosure off keeps the old shape, with only the brand configurable.
    legacy = render_voice_append("Acme", ai_disclosure=False)
    assert "Namaste, Acme se baat kar rahe hain" in legacy
    assert "WorkAssign AI" in VOICE_APPEND  # module constant = default brand


# --- audio cache ---------------------------------------------------------------------------------------


@pytest.fixture
def cache_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", True)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-1")
    monkeypatch.setattr(settings, "elevenlabs_model_id", "model-1")


async def test_cache_fills_once_in_the_background_and_serves_hits(cache_on: None) -> None:
    calls: list[str] = []

    async def synth(text: str) -> list[str]:
        calls.append(text)
        await asyncio.sleep(0.01)
        return ["AA==", "BB=="]

    cache = GreetingAudioCache(synth=synth)
    assert cache.get(ORG_A, "hello") is None
    cache.ensure_in_background(ORG_A, "hello")
    cache.ensure_in_background(ORG_A, "hello")  # already in flight: single-flight
    await asyncio.sleep(0.1)
    hit = cache.get(ORG_A, "hello")
    assert hit is not None and hit.chunks == ("AA==", "BB==") and calls == ["hello"]
    cache.ensure_in_background(ORG_A, "hello")  # already cached: no second synthesis
    await asyncio.sleep(0.05)
    assert calls == ["hello"]


async def test_cache_is_isolated_per_org_and_per_voice(cache_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def synth(text: str) -> list[str]:
        return ["AA=="]

    cache = GreetingAudioCache(synth=synth)
    cache.ensure_in_background(ORG_A, "same text")
    await asyncio.sleep(0.05)
    assert cache.get(ORG_A, "same text") is not None
    assert cache.get(ORG_B, "same text") is None  # another org never sees it
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-2")
    assert cache.get(ORG_A, "same text") is None  # a new voice must not replay the old one


async def test_failed_synthesis_caches_nothing_and_can_retry(cache_on: None) -> None:
    attempts = 0

    async def synth(text: str) -> list[str]:
        nonlocal attempts
        attempts += 1
        return [] if attempts == 1 else ["AA=="]

    cache = GreetingAudioCache(synth=synth)
    cache.ensure_in_background(ORG_A, "hi")
    await asyncio.sleep(0.05)
    assert cache.get(ORG_A, "hi") is None
    cache.ensure_in_background(ORG_A, "hi")
    await asyncio.sleep(0.05)
    assert cache.get(ORG_A, "hi") is not None


async def test_cache_never_fills_when_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", False)
    called = False

    async def synth(text: str) -> list[str]:
        nonlocal called
        called = True
        return ["AA=="]

    cache = GreetingAudioCache(synth=synth)
    cache.ensure_in_background(ORG_A, "hi")
    await asyncio.sleep(0.05)
    assert not called and cache.get(ORG_A, "hi") is None


async def test_cache_evicts_the_oldest_entry(cache_on: None) -> None:
    async def synth(text: str) -> list[str]:
        return [text]

    cache = GreetingAudioCache(max_entries=2, synth=synth)
    for text in ("one", "two", "three"):
        cache.ensure_in_background(ORG_A, text)
        await asyncio.sleep(0.02)
    assert cache.get(ORG_A, "one") is None
    assert cache.get(ORG_A, "two") is not None and cache.get(ORG_A, "three") is not None


# --- playing a cached greeting through the adapter ------------------------------------------------------


class _Phone:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Agent:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Log:
    def info(self, *a: Any, **k: Any) -> None:
        return None

    warning = info


async def test_cached_greeting_plays_audio_tells_the_model_and_guards_against_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted: list[tuple[str, Any]] = []

    async def fake_persist(state: Any, text: str, *, pending: Any = None) -> None:
        persisted.append((text, pending))

    monkeypatch.setattr(adapter, "_persist_voice_turn", fake_persist)
    state = adapter.CallState(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=ORG_A)
    phone, agent = _Phone(), _Agent()

    await adapter.play_cached_greeting(
        phone, agent, state, "Namaste, main Acme ka AI assistant hoon.", ("A" * 12000, "B" * 12000), _Log()  # type: ignore[arg-type]
    )

    assert [m["media"]["payload"] for m in phone.sent if m.get("event") == "playAudio"] == ["A" * 12000, "B" * 12000]
    (item,) = agent.sent  # the model learns what was said, and is NOT asked to speak
    assert item["type"] == "conversation.item.create"
    assert item["item"]["role"] == "assistant"
    assert item["item"]["content"][0]["text"] == "Namaste, main Acme ka AI assistant hoon."
    # The callee's reflexive "hello?" is protected until the audio has played.
    assert state.greeting_guard_until == state.playback_end > time.monotonic()
    assert state.greeting_guard_extended
    assert persisted == [("Namaste, main Acme ka AI assistant hoon.", None)]
    assert state.last_spoken_text.startswith("Namaste")
