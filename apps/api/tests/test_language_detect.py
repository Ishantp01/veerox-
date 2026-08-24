"""Tests for apps.api.channels.voice.language_detect — the script-range
detector (pure, no network) and the LLM-fallback classifier (network call
monkeypatched at the same seam test_llm.py uses: llm._create_completion).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from apps.api.channels.voice import language_detect
from apps.api.core import llm

# ---------------------------------------------------------------------------
# detect_from_name — a caller stating the language directly, resolved
# without ever touching script or the LLM.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Marathi", "Marathi"),
        ("marathi", "Marathi"),
        ("Hindi please", "Hindi"),
        ("English.", "English"),
        ("hindi mein baat karo", "Hindi"),
    ],
)
def test_detect_from_name_matches(text: str, expected: str) -> None:
    assert language_detect.detect_from_name(text) == expected


def test_detect_from_name_no_match_returns_none() -> None:
    assert language_detect.detect_from_name("haan") is None
    assert language_detect.detect_from_name("") is None


def test_detect_from_name_two_names_is_ambiguous() -> None:
    # Naming two languages ("a bit of Hindi, a bit of English") is a
    # mixed-language signal, not a single clean answer - leave it to the
    # LLM layer rather than guessing whichever name came first.
    assert language_detect.detect_from_name("thoda hindi thoda english chalega") is None


# ---------------------------------------------------------------------------
# detect_from_script — script-unique languages should resolve with zero
# network involvement; shared-script text should come back None.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("வணக்கம்", "Tamil"),
        ("నమస్కారం", "Telugu"),
        ("ನಮಸ್ಕಾರ", "Kannada"),
        ("നമസ്കാരം", "Malayalam"),
        ("નમસ્તે", "Gujarati"),
        ("ਸਤ ਸ੍ਰੀ ਅਕਾਲ", "Punjabi"),
        ("ନମସ୍କାର", "Odia"),
        ("السلام عليكم", "Urdu"),
        ("নমস্কার", "Bengali"),
    ],
)
def test_detect_from_script_unique_scripts(text: str, expected: str) -> None:
    assert language_detect.detect_from_script(text) == expected


def test_detect_from_script_assamese_by_special_letter() -> None:
    # Real Bengali-block text plus the Assamese-only ra (ৰ) — Bengali
    # doesn't have this letter at all, so its presence is a solid signal.
    text = "মই ভাল আছোঁ" + "ৰ"
    assert language_detect.detect_from_script(text) == "Assamese"


def test_detect_from_script_manipuri_meetei_mayek() -> None:
    text = "ꯀꯅꯐ"
    assert language_detect.detect_from_script(text) == "Manipuri"


def test_detect_from_script_santali_ol_chiki() -> None:
    text = "᱐᱑᱒"
    assert language_detect.detect_from_script(text) == "Santali"


def test_detect_from_script_devanagari_is_ambiguous() -> None:
    # Hindi/Marathi/Nepali/Sanskrit/Maithili/Bodo/Konkani/Dogri all share
    # this script — script alone can't tell them apart.
    assert language_detect.detect_from_script("नमस्ते") is None


def test_detect_from_script_latin_is_ambiguous() -> None:
    # English and Hinglish (romanized Hindi) are both Latin script.
    assert language_detect.detect_from_script("hello how are you") is None


def test_detect_from_script_empty_text() -> None:
    assert language_detect.detect_from_script("") is None


# ---------------------------------------------------------------------------
# classify_via_llm — monkeypatch the same seam test_llm.py uses so no real
# network call happens.
# ---------------------------------------------------------------------------


@dataclass
class _FakeMessage:
    content: str | None
    tool_calls: list[Any] | None = None


@dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = "stop"


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 2


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]
    usage: _FakeUsage


def _patch_llm_reply(monkeypatch: pytest.MonkeyPatch, reply: str | None) -> None:
    async def fake(**_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            choices=[_FakeChoice(message=_FakeMessage(content=reply))],
            usage=_FakeUsage(),
        )

    monkeypatch.setattr(llm, "_create_completion", fake)


async def test_classify_via_llm_parses_known_language(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm_reply(monkeypatch, "Hindi")
    assert await language_detect.classify_via_llm("haan bilkul") == "Hindi"


async def test_classify_via_llm_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm_reply(monkeypatch, "hindi")
    assert await language_detect.classify_via_llm("haan bilkul") == "Hindi"


async def test_classify_via_llm_strips_trailing_period(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm_reply(monkeypatch, "Hinglish.")
    assert await language_detect.classify_via_llm("theek hai yaar") == "Hinglish"


async def test_classify_via_llm_unclear_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm_reply(monkeypatch, "Unclear")
    assert await language_detect.classify_via_llm("okay") is None


async def test_classify_via_llm_unrecognized_reply_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm_reply(monkeypatch, "I'm not sure, could be several languages")
    assert await language_detect.classify_via_llm("hmm") is None


async def test_classify_via_llm_empty_text_skips_call(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    async def fake(**_kwargs: Any) -> _FakeResponse:
        nonlocal called
        called = True
        return _FakeResponse(choices=[_FakeChoice(message=_FakeMessage(content="Hindi"))], usage=_FakeUsage())

    monkeypatch.setattr(llm, "_create_completion", fake)
    assert await language_detect.classify_via_llm("   ") is None
    assert called is False


async def test_classify_via_llm_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> _FakeResponse:
        raise RuntimeError("network exploded")

    monkeypatch.setattr(llm, "_create_completion", fake)
    assert await language_detect.classify_via_llm("some text") is None


# ---------------------------------------------------------------------------
# detect_caller_language — script first, LLM fallback only when needed.
# ---------------------------------------------------------------------------


async def test_detect_caller_language_uses_script_without_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> Any:
        raise AssertionError("LLM fallback should not be called when script resolves")

    monkeypatch.setattr(llm, "_create_completion", fail_if_called)

    assert await language_detect.detect_caller_language("வணக்கம்") == "Tamil"


async def test_detect_caller_language_uses_name_without_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**_kwargs: Any) -> Any:
        raise AssertionError("LLM fallback should not be called when the name matches")

    monkeypatch.setattr(llm, "_create_completion", fail_if_called)

    assert await language_detect.detect_caller_language("Marathi") == "Marathi"


async def test_detect_caller_language_falls_back_to_llm_for_devanagari(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm_reply(monkeypatch, "Marathi")
    assert await language_detect.detect_caller_language("नमस्ते, कसे आहात?") == "Marathi"


async def test_detect_caller_language_falls_back_to_llm_for_latin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm_reply(monkeypatch, "English")
    assert await language_detect.detect_caller_language("good morning") == "English"
