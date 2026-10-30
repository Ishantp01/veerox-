"""Natural-speech Phase 2 (test-org only): every new option is off by default and changes nothing until an org's
voice profile sets it. No network, no audio, no provider call."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice.voice_profile import (
    VoiceProfile,
    prompt_extras,
    prompt_options,
    resolve_profile,
)
from apps.api.core.prompts import (
    VOICE_APPEND,
    VOICE_NATURAL_SPEECH,
    VOICE_SCRIPT_DEVANAGARI,
    VOICE_SCRIPT_ROMAN,
    render_voice_append,
)

EVAL_DIR = Path(__file__).resolve().parents[3] / "docs" / "natural-speech-eval"


# --- defaults: nothing changes unless an org opts in -----------------------------------------------------------


def test_new_profile_fields_default_to_off() -> None:
    p = VoiceProfile()
    assert (p.natural_speech, p.hindi_script, p.phrase_flush, p.phrase_min_chars, p.phrase_soft_chars) == (
        False, None, None, None, None,
    )
    assert resolve_profile(None) is None and resolve_profile({}) is None
    assert prompt_extras(None) == (False, None)
    assert prompt_extras({"speaking_style": True}) == (False, None)


def test_fingerprint_is_unchanged_for_profiles_that_do_not_use_the_new_fields() -> None:
    raw = {"voice_id": "OrgAVoice12345", "normalize_speech": True}
    r = resolve_profile(raw)
    old = hashlib.sha256(
        json.dumps(
            [r.voice_id, r.model_id, r.voice_settings, True, None, [], None], sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()
    assert r.fingerprint == old
    # Prompt-only options do not alter the audio of a fixed text, so they do not alter the fingerprint.
    assert resolve_profile({**raw, "natural_speech": True, "hindi_script": "roman"}).fingerprint == old
    # Phrase cutting does.
    assert resolve_profile({**raw, "phrase_flush": True}).fingerprint != old


def test_invalid_values_are_rejected_and_never_fail_a_call() -> None:
    assert resolve_profile({"hindi_script": "klingon"}) is None
    assert resolve_profile({"phrase_min_chars": 1}) is None
    assert resolve_profile({"phrase_min_chars": 500}) is None
    assert prompt_extras({"hindi_script": "klingon"}) == (False, None)


def test_existing_prompt_options_shape_is_unchanged() -> None:
    assert prompt_options(None) == (False, None, False)
    assert prompt_options({"speaking_style": True, "tone": "friendly", "natural_hinglish": True}) == (True, "friendly", True)
    assert prompt_extras({"natural_speech": True, "hindi_script": "devanagari"}) == (True, "devanagari")


# --- prompt ----------------------------------------------------------------------------------------------------


def test_prompt_is_byte_identical_when_the_options_are_off() -> None:
    assert render_voice_append("Acme") == render_voice_append("Acme", natural_speech=False, hindi_script=None)
    assert render_voice_append("WorkAssign AI") == VOICE_APPEND
    assert render_voice_append("Acme", hindi_script="not-a-script") == render_voice_append("Acme")


def test_natural_speech_and_script_blocks_are_appended_and_keep_the_base_rules() -> None:
    base = render_voice_append("Acme", ai_disclosure=True)
    on = render_voice_append("Acme", ai_disclosure=True, natural_speech=True, hindi_script="devanagari")
    assert on.startswith(base.rstrip("\n"))  # nothing in the base tail (AI disclosure, tool rules) was removed
    assert VOICE_NATURAL_SPEECH.strip() in on and VOICE_SCRIPT_DEVANAGARI.strip() in on
    assert VOICE_SCRIPT_ROMAN.strip() not in on
    roman = render_voice_append("Acme", hindi_script="roman")
    assert VOICE_SCRIPT_ROMAN.strip() in roman and VOICE_SCRIPT_DEVANAGARI.strip() not in roman


def test_script_blocks_keep_english_and_brand_words_and_do_not_convert_everything() -> None:
    assert "Latin" in VOICE_SCRIPT_DEVANAGARI and "brand names" in VOICE_SCRIPT_DEVANAGARI
    assert "Do not translate or transliterate" in " ".join(VOICE_SCRIPT_DEVANAGARI.split())
    roman = " ".join(VOICE_SCRIPT_ROMAN.split())
    assert "Do not write Devanagari" in roman and "brand names" in roman


def test_natural_speech_block_forbids_written_out_fillers_and_does_not_ask_for_them() -> None:
    text = VOICE_NATURAL_SPEECH
    assert "Never write" in text and "umm" in text  # mentioned only to forbid it
    assert "stage directions" in text
    assert "script, facts, figures and tool rules stay as they are" in text
    assert not re.search(r"(add|insert|use) (a )?(filler|breath)", text, re.I)


# --- phrase buffer / flush override ----------------------------------------------------------------------------


def test_phrase_buffer_defaults_are_unchanged() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("Ji haan. ") == ""  # 8 characters: held, as before
    assert buf.flush() == "Ji haan. "


def test_shorter_minimum_releases_a_short_acknowledgement_on_its_own() -> None:
    buf = elevenlabs_client.PhraseBuffer(min_chars=8)
    assert buf.feed("Ji haan. Aur ") == "Ji haan. "
    assert buf.flush() == "Aur "


def test_short_fragments_are_joined_to_the_next_phrase_by_default() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    out = buf.feed("Haan ji. Aapka ghar kitne BHK ka hai? Aur")
    assert out == "Haan ji. Aapka ghar kitne BHK ka hai? "  # "Haan ji." not spoken alone


def test_devanagari_danda_and_question_mark_end_phrases() -> None:
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("जी बिल्कुल, आपका काम कल सुबह तक हो जाएगा। आगे ") == "जी बिल्कुल, आपका काम कल सुबह तक हो जाएगा। "
    assert elevenlabs_client.PhraseBuffer().feed("क्या आप इस हफ़्ते free हैं? हाँ") == "क्या आप इस हफ़्ते free हैं? "


def _session(raw: dict | None) -> elevenlabs_client.ElevenLabsTTSSession:
    return elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile(raw))


def test_profile_phrase_flush_overrides_the_platform_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    flag = elevenlabs_client.settings
    monkeypatch.setattr(flag, "voice_tts_phrase_flush", False)
    assert _session(None)._phrases is None
    assert _session({"speed": 1.0})._phrases is None  # unset follows the platform
    assert _session({"phrase_flush": True})._phrases is not None
    monkeypatch.setattr(flag, "voice_tts_phrase_flush", True)
    assert _session(None)._phrases is not None
    assert _session({"phrase_flush": False})._phrases is None  # an org can opt out too


def test_profile_phrase_sizes_reach_the_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    buf = _session({"phrase_flush": True, "phrase_min_chars": 8, "phrase_soft_chars": 25})._phrases
    assert buf is not None and buf._min_chars == 8 and buf._soft_chars == 25
    default = _session({"phrase_flush": True})._phrases
    assert default is not None and (default._min_chars, default._soft_chars) == (12, 40)


# --- evaluation assets -----------------------------------------------------------------------------------------


def _phrases() -> list[dict]:
    return json.loads((EVAL_DIR / "phrases.json").read_text(encoding="utf-8"))["phrases"]


def test_eval_phrases_are_valid_and_paired_across_scripts() -> None:
    phrases = _phrases()
    ids = [p["id"] for p in phrases]
    assert len(ids) == len(set(ids))
    cats = {p["category"] for p in phrases}
    assert {"greeting", "simple_question", "long_explanation", "follow_up", "hindi", "hinglish",
            "acknowledgement"} <= cats
    pairs: dict[str, dict[str, str]] = {}
    for p in phrases:
        pairs.setdefault(p["pair"], {})[p["script"]] = p["text"]
    for name, scripts in pairs.items():
        if name == "english":
            continue
        assert set(scripts) == {"roman", "devanagari"}, name
        assert re.search(r"[ऀ-ॿ]", scripts["devanagari"]), name
        assert not re.search(r"[ऀ-ॿ]", scripts["roman"]), name


def test_english_and_brand_words_are_kept_identical_in_both_scripts() -> None:
    by_pair: dict[str, dict[str, str]] = {}
    for p in _phrases():
        by_pair.setdefault(p["pair"], {})[p["script"]] = p["text"]
    for name, scripts in by_pair.items():
        if "devanagari" not in scripts:
            continue
        latin = re.findall(r"[A-Za-z]+", scripts["devanagari"])
        roman_words = {w.lower() for w in re.findall(r"[A-Za-z]+", scripts["roman"])}
        assert all(w.lower() in roman_words for w in latin), (name, latin)


def test_eval_candidates_are_valid_profiles_and_do_not_change_voice_or_style() -> None:
    cands = json.loads((EVAL_DIR / "candidates.json").read_text(encoding="utf-8"))
    for c in cands:
        VoiceProfile.model_validate(c["profile"])
        assert "voice_id" not in c["profile"]  # the platform voice is held fixed
        assert not {"stability", "similarity_boost", "style"} & set(c["profile"])


# --- review additions: isolation, no unsupported provider fields, boundaries, experiment accounting -------------

import importlib.util  # noqa: E402

from apps.api.channels.voice.tts_normalize import normalize_text  # noqa: E402
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS  # noqa: E402


def test_two_orgs_never_share_phrase_state_or_prompt_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    org_a = {"voice_id": "OrgAVoice12345", "phrase_flush": True, "phrase_min_chars": 8,
             "natural_speech": True, "hindi_script": "devanagari"}
    org_b = {"voice_id": "OrgBVoice12345"}
    for _ in range(2):  # alternate, as concurrent calls would
        a, b = _session(org_a), _session(org_b)
        assert a._phrases is not None and b._phrases is None
        assert prompt_extras(org_a) == (True, "devanagari") and prompt_extras(org_b) == (False, None)
        assert VOICE_SCRIPT_DEVANAGARI.strip() in render_voice_append("A", natural_speech=True, hindi_script="devanagari")
        assert VOICE_SCRIPT_DEVANAGARI.strip() not in render_voice_append("B")
    assert _session(org_a)._phrases is not _session(org_a)._phrases  # one buffer per session, never shared
    assert resolve_profile(org_a).fingerprint != resolve_profile(org_b).fingerprint


@pytest.mark.asyncio
async def test_no_unsupported_provider_fields_are_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Context-aware TTS is NOT implemented: the only keys ever sent are the ones the integration already used."""
    fake_ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        assert "/stream-input?" in url and "multi-stream-input" not in url  # the single-context endpoint only
        assert not re.search(r"previous|next_text|context", url)
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    voice = resolve_profile({"phrase_flush": True, "natural_speech": True, "hindi_script": "roman"})
    async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
        for piece in ["Haan ji. ", "Aapka ghar ", "kitne BHK ka hai? ", "Aur"]:
            await session.send_text(piece)
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    keys = {k for msg in fake_ws.sent for k in msg}
    assert keys <= {"text", "voice_settings", "xi_api_key", "generation_config", "flush"}
    source = Path(elevenlabs_client.__file__).read_text(encoding="utf-8")
    assert not re.search(r"previous_text|next_text|previous_request_ids|context_id", source)


@pytest.mark.asyncio
async def test_incomplete_phrase_is_spoken_at_finish_and_nothing_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return fake_ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    async with elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile({"phrase_flush": True})) as s:
        for piece in ["Dekhiye, ", "modular kitchen ", "mein pehle naap ", "lete hain aur phir"]:
            await s.send_text(piece)
        await s.finish()
        _ = [c async for c in s.audio_chunks()]
    spoken = "".join(str(m.get("text", "")) for m in fake_ws.sent[1:])
    assert spoken.split() == "Dekhiye, modular kitchen mein pehle naap lete hain aur phir".split()
    assert fake_ws.sent[-1] == {"text": ""}


@pytest.mark.parametrize(
    ("chunks", "expected_first"),
    [
        (["Haan ji, ", "samajh gaya."], ""),  # a short acknowledgement alone waits for the end of the reply
        (["Haan ji. ", "Aapka ghar kitne BHK ka hai? ", "Aur"], "Haan ji. Aapka ghar kitne BHK ka hai? "),
        (["Achha… ", "theek hai ji, main dekhta hoon. ", "Phir"], "Achha… theek hai ji, main dekhta hoon. "),
        (["Plan 3.5 lakh ka hai aur isme sab kuch aa jaata hai. ", "Theek?"], "Plan 3.5 lakh ka hai aur isme sab kuch aa jaata hai. "),
        (["Aapka bill Rs. 1500 hai aur advance dena hoga. ", "Theek"], "Aapka bill Rs. 1500 hai aur advance dena hoga. "),
        (["जी बिल्कुल, आपका काम कल हो जाएगा। ", "और कुछ?"], "जी बिल्कुल, आपका काम कल हो जाएगा। "),
    ],
)
def test_phrase_boundary_cases(chunks: list[str], expected_first: str) -> None:
    buf = elevenlabs_client.PhraseBuffer()
    released = [r for r in (buf.feed(c) for c in chunks) if r]
    assert (released[0] if released else "") == expected_first
    assert "".join(released) + buf.flush() == "".join(chunks)  # never drops or repeats text


def test_known_limit_text_without_sentence_punctuation_is_held_until_the_end() -> None:
    """Documented risk (not fixed): with phrase flush on, an unpunctuated stretch is only released at the end of the
    reply, so the first audio waits for the whole reply. Pinned so a future max-hold change is deliberate."""
    buf = elevenlabs_client.PhraseBuffer()
    assert buf.feed("bolna " * 40) == ""
    # A danda glued to the next word (no space) is not a boundary either, unlike a danda followed by a space.
    assert elevenlabs_client.PhraseBuffer().feed("जी बिल्कुल, आपका काम कल हो जाएगा।आगे बात करते हैं") == ""


def test_text_normaliser_keeps_english_and_brand_words_in_every_script() -> None:
    mixed = "आपका appointment कल 5 बजे confirm हो गया है, WhatsApp पर details भेज रहा हूँ।"
    out = normalize_text(mixed)
    for word in ("appointment", "confirm", "WhatsApp", "details"):
        assert word in out
    assert "पाँच बजे" in out  # only the number was respelled; Devanagari stays Devanagari
    roman = normalize_text("Sharma Interiors se Veer bol raha hoon, 2:30 PM par.")
    assert "Sharma Interiors" in roman and "Veer" in roman and not re.search(r"[\u0900-\u097F]", roman)


def test_natural_speech_block_does_not_override_the_ai_disclosure_when_it_was_cut_off() -> None:
    text = " ".join(VOICE_NATURAL_SPEECH.split())
    assert "once the caller has heard it" in text and "say it as instructed" in text


def _sheet_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "natural_speech_sheet.py"
    spec = importlib.util.spec_from_file_location("natural_speech_sheet", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_experiment_job_list_matches_the_character_totals_and_is_reproducible() -> None:
    sheet = _sheet_module()
    phrases, cands = sheet.load()
    a = sheet.jobs(phrases, cands, seed=7, takes=1)
    b = sheet.jobs(phrases, cands, seed=7, takes=1)
    assert a == b  # same seed, same order, same blind ids
    assert [j["blind_id"] for j in a] != [j["blind_id"] for j in sheet.jobs(phrases, cands, seed=8, takes=1)]
    assert len({j["blind_id"] for j in a}) == len(a)
    assert sum(j["chars"] for j in a) == 6674 and len(a) == 78
    assert sum(j["chars"] for j in sheet.jobs(phrases, cands, seed=7, takes=3)) == 3 * 6674
    # Fair comparison: E2 compares the three flush configs on exactly the same phrases.
    e2 = {c: {j["phrase_id"] for j in a if j["experiment"] == "E2" and j["candidate"] == c} for c in ("N0", "N1", "N2", "N4")}
    assert e2["N0"] == e2["N1"] == e2["N2"] == e2["N4"] and len(e2["N0"]) == 11
    assert all(j["chars"] == len(next(p["text"] for p in phrases if p["id"] == j["phrase_id"])) for j in a)
