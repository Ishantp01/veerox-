"""Phase 7B: hold-log privacy, spoken-time fix, Hinglish/tone prompt options, chunk schedule, tenant isolation.

Offline. These prove what our text layer and prompt builder produce and send; how any of it SOUNDS needs a
listener, and the ElevenLabs settings noted below are as documented, not tested against the live service.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import elevenlabs_client, turn_controller
from apps.api.channels.voice.tts_normalize import TTSNormalizer, normalize_text
from apps.api.channels.voice.voice_profile import (
    VoiceProfileError,
    parse_profile,
    prompt_options,
    resolve_profile,
)
from apps.api.config import settings
from apps.api.core.prompts import VOICE_APPEND, VOICE_NATURAL_HINGLISH, VOICE_TONES, render_voice_append
from apps.api.tests.test_admin_endpoints import ADMIN_HEADERS, _seed_org, client, fake_redis  # noqa: F401
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS
from apps.api.tests.test_voice_interruption import Call, _flags, _RealElevenLabsSession, base  # noqa: F401  (autouse fixture)


@pytest.fixture(autouse=True)
def _platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "PlatformVoice0001")
    monkeypatch.setattr(settings, "elevenlabs_model_id", "eleven_flash_v2_5")


# --- 7A follow-up: the hold log carries reasons and durations, never words ------------------------------------------------------


class _CaptureLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, *a: Any, **k: Any) -> None:
        self.records.append((event, k))

    warning = error = info


@pytest.mark.parametrize(
    ("text", "busy", "smart", "reason"),
    [
        ("ek minute", False, True, "thinking_request"),
        ("haan", False, False, "lone_filler"),
        ("haan mera matlab...", False, False, "trailing_punctuation"),
        ("mujhe lagta hai ki", False, False, "continuation_word"),
        ("Mera budget", False, True, "unfinished_phrase"),
    ],
)
def test_every_hold_has_a_named_reason_and_its_duration_comes_from_one_place(
    text: str, busy: bool, smart: bool, reason: str
) -> None:
    seconds, why = turn_controller.hold_decision(text, busy, smart)
    assert why == reason and seconds > 0
    assert seconds == {
        "thinking_request": turn_controller.HOLD_THINKING_SECONDS,
        "lone_filler": turn_controller.HOLD_LONE_FILLER_SECONDS,
    }.get(reason, turn_controller.HOLD_CONTINUATION_SECONDS)


def test_a_finished_turn_has_no_hold_and_no_reason() -> None:
    assert turn_controller.hold_decision("Investment kitna lagega?", False, True) == (0.0, None)
    assert turn_controller.hold_decision("", False, True) == (0.0, None)


async def test_the_hold_log_line_never_contains_what_the_caller_said(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=True)
    call = Call()
    call.log = _CaptureLog()
    call.state.live = type("Live", (), {"healthy": True})()
    call.state.utterance_text = "Mera budget paanch lakh... ek minute"
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    holds = [k for name, k in call.log.records if name == "voice_endpoint_hold"]
    assert len(holds) == 1
    assert holds[0]["reason"] == "thinking_request" and holds[0]["seconds"] == turn_controller.HOLD_THINKING_SECONDS
    assert holds[0]["words"] == 6 and "text" not in holds[0]
    assert "paanch" not in json.dumps(call.log.records, default=str)


async def test_with_the_flags_off_there_is_no_hold_and_no_hold_log(monkeypatch: pytest.MonkeyPatch) -> None:
    _flags(monkeypatch, voice_smart_turn_taking=False, voice_endpoint_hold=False)
    call = Call()
    call.log = _CaptureLog()
    call.state.live = type("Live", (), {"healthy": True})()
    call.state.utterance_text = "ek minute"
    await call.event("input_audio_buffer.speech_stopped")
    await call.event("input_audio_buffer.committed")
    await asyncio.sleep(0.05)
    assert not [r for r in call.log.records if r[0] == "voice_endpoint_hold"] and len(call.replies()) == 1


# --- spoken time: "baje" is said once ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Meeting 12:00 baje hai", "Meeting baarah baje hai"),
        ("aaj shaam 6:30 baje", "aaj shaam saadhe chhah baje"),
        ("kal 3:20 baje", "kal teen bajkar bees minute"),
        ("kal 12 baje convenient rahega?", "kal baarah baje convenient rahega?"),
        ("Kal subah 10 baje", "Kal subah das baje"),
        ("मीटिंग 2:30 बजे", "मीटिंग ढाई बजे"),
        ("Meeting 5 बजे है", "Meeting पाँच बजे है"),
    ],
)
def test_baje_is_not_said_twice(text: str, expected: str) -> None:
    assert normalize_text(text, language=None) == expected


def test_english_times_keep_their_own_words() -> None:
    assert normalize_text("See you at 3:30 pm sharp") == "See you at three thirty p m sharp"


# --- other spoken-form guarantees (pin existing behaviour) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Aapka budget ₹5,00,000 hai na?", "Aapka budget paanch lakh rupaye hai na?"),
        ("बजट ₹7 लाख है", "बजट सात लाख रुपये है"),
        ("Around 12% yearly", "Around twelve percent yearly"),
        ("Appointment 25 October ko 3:30 PM par hai.", "Appointment pachchees October ko dopahar saadhe teen baje par hai."),
        ("Aaj 19/10/2026 hai", "Aaj unnees October do hazaar chhabbees hai"),
        ("Aapka number 9876543210 hai?", "Aapka number nau aath saat chhah paanch, chaar teen do ek shunya hai?"),
        ("OTP 4829 hai", "O T P chaar aath do nau hai"),
        ("2BHK flat 1200 sq ft ka hai", "do B H K flat ek hazaar do sau square feet ka hai"),
        ("ROI kaafi achha hai, GST alag se.", "R O I kaafi achha hai, G S T alag se."),
    ],
)
def test_amounts_dates_numbers_and_acronyms_are_spoken_in_the_callers_language(text: str, expected: str) -> None:
    assert normalize_text(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Aapka naam Rajesh Sharma hai, sahi?", "Dava India se Priyanka Verma bol rahi hoon.", "MG Road, Jabalpur", "Namaste, kaise hain?"],
)
def test_names_and_places_are_left_exactly_as_written(text: str) -> None:
    assert normalize_text(text) == text


@pytest.mark.parametrize("text", ["", " ", "...", "?", "​"])
def test_empty_or_punctuation_only_text_does_not_break(text: str) -> None:
    assert normalize_text(text) == text


def test_text_that_already_contains_our_placeholder_range_is_returned_untouched() -> None:
    odd = "Rs 500  baje"
    assert normalize_text(odd) == odd


def test_an_org_pronunciation_beats_every_other_rule() -> None:
    assert normalize_text("Dava India 12 baje", pronunciations=(("Dava", "Daawa"),)) == "Daawa India baarah baje"


def test_streamed_deltas_give_the_same_speech_as_the_whole_sentence() -> None:
    sentence = "Kal 12:30 baje aapka appointment hai, Rs 5,000 lagega."
    stream = TTSNormalizer(enabled=True)
    out = "".join(stream.feed(sentence[i : i + 3]) for i in range(0, len(sentence), 3)) + stream.flush()
    assert out == normalize_text(sentence)


def test_a_disabled_normalizer_is_a_pass_through_with_no_buffering() -> None:
    n = TTSNormalizer(enabled=False)
    assert n.feed("Rs 5,000 ") == "Rs 5,000 " and n.flush() == ""


# --- profile: what is accepted, and what is rejected rather than silently ignored ----------------------------------------


def test_style_is_rejected_for_flash_because_it_is_not_documented_for_that_model() -> None:
    with pytest.raises(VoiceProfileError, match="style is only supported"):
        parse_profile({"style": 0.4})  # platform model is eleven_flash_v2_5
    with pytest.raises(VoiceProfileError, match="style is only supported"):
        parse_profile({"style": 0.4, "model_id": "eleven_turbo_v2_5"})
    assert parse_profile({"style": 0.4, "model_id": "eleven_multilingual_v2"}).style == 0.4


@pytest.mark.parametrize("bad", [{"speed": 0.5}, {"speed": 1.3}, {"stability": 1.5}, {"unknown_setting": 1}, {"model_id": "eleven_v3"}])
def test_unsupported_values_and_unknown_fields_are_errors(bad: dict[str, Any]) -> None:
    with pytest.raises(VoiceProfileError):
        parse_profile(bad)


@pytest.mark.parametrize("schedule", [[49], [501], [], [50] * 7, [True], [120.5]])
def test_a_bad_chunk_schedule_is_rejected(schedule: list[Any]) -> None:
    with pytest.raises(VoiceProfileError):
        parse_profile({"chunk_schedule": schedule})


def test_a_documented_chunk_schedule_is_accepted_and_resolved() -> None:
    resolved = resolve_profile({"chunk_schedule": [50, 90, 160, 250]})
    assert resolved is not None and resolved.chunk_schedule == (50, 90, 160, 250)


def test_the_chunk_schedule_changes_the_audio_fingerprint() -> None:
    a, b = resolve_profile({"normalize_speech": True}), resolve_profile({"normalize_speech": True, "chunk_schedule": [60]})
    assert a is not None and b is not None and a.audio_fingerprint() != b.audio_fingerprint()


async def _handshake(profile: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    ws = _FakeElevenLabsWS(server_messages=[{"audio": "YWJj", "isFinal": True}])

    async def fake_connect(url: str) -> _FakeElevenLabsWS:
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    async with _RealElevenLabsSession(profile=profile) as session:
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    return ws.sent[0]


async def test_the_handshake_is_unchanged_without_a_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(await _handshake(None, monkeypatch)) == {"text", "voice_settings", "xi_api_key"}
    assert "generation_config" not in await _handshake(resolve_profile({"speed": 1.1}), monkeypatch)


async def test_a_schedule_is_sent_once_in_the_handshake_only(monkeypatch: pytest.MonkeyPatch) -> None:
    first = await _handshake(resolve_profile({"chunk_schedule": [50, 90, 160, 250]}), monkeypatch)
    assert first["generation_config"] == {"chunk_length_schedule": [50, 90, 160, 250]}


# --- prompt options -----------------------------------------------------------------------------------------------------------------


def test_the_default_prompt_is_byte_identical_to_before() -> None:
    assert render_voice_append("WorkAssign AI") == VOICE_APPEND
    assert render_voice_append("X", tone=None, natural_hinglish=False) == render_voice_append("X")
    assert render_voice_append("X", tone="not_a_tone") == render_voice_append("X")


def test_hinglish_guidance_and_tone_are_added_only_when_asked_for() -> None:
    base_text = render_voice_append("Acme")
    rich = render_voice_append("Acme", natural_hinglish=True, tone="calm_reassuring")
    assert rich.startswith(base_text.rstrip("\n")) and VOICE_NATURAL_HINGLISH.strip() in rich
    assert VOICE_TONES["calm_reassuring"] in rich and "never what is true or allowed" in rich


def test_the_ai_disclosure_example_survives_every_option() -> None:
    for tone in (None, *VOICE_TONES):
        text = render_voice_append("Acme", ai_disclosure=True, speaking_style=True, natural_hinglish=True, tone=tone)
        assert "AI assistant" in text


def test_the_guidance_does_not_license_changing_facts() -> None:
    assert "never change the" in VOICE_NATURAL_HINGLISH and "Do not add promises" in VOICE_NATURAL_HINGLISH
    assert "do not claim to sense" in VOICE_TONES["calm_reassuring"]


def test_prompt_options_follow_the_stored_profile_and_default_to_off() -> None:
    assert prompt_options(None) == (False, None, False)
    assert prompt_options({"bogus": 1}) == (False, None, False)  # invalid -> ignored, never fails a call
    assert prompt_options({"speaking_style": True, "tone": "friendly", "natural_hinglish": True}) == (True, "friendly", True)


def test_two_orgs_get_independent_options_and_fingerprints() -> None:
    a = {"tone": "friendly", "normalize_speech": True, "voice_id": "AAAAAAAAAA01"}
    b = {"tone": "calm_reassuring", "normalize_speech": True, "voice_id": "BBBBBBBBBB02", "chunk_schedule": [60]}
    ra, rb = resolve_profile(a), resolve_profile(b)
    assert ra is not None and rb is not None
    assert (ra.tone, rb.tone) == ("friendly", "calm_reassuring") and ra.chunk_schedule is None and rb.chunk_schedule == (60,)
    assert ra.audio_fingerprint() != rb.audio_fingerprint()
    assert prompt_options(a)[1] == "friendly" and prompt_options(b)[1] == "calm_reassuring"  # no shared state


# --- admin API ---------------------------------------------------------------------------------------------------------------------------


async def test_the_default_effective_profile_reports_the_new_options_as_off(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    body = (await client.get("/admin/settings/voice-profile", headers=ADMIN_HEADERS)).json()
    assert body["profile"] is None
    assert (body["effective"]["tone"], body["effective"]["natural_hinglish"], body["effective"]["chunk_schedule"]) == (None, False, None)


async def test_the_new_options_can_be_saved_and_a_bad_schedule_is_refused(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    good = {"tone": "confident_informative", "natural_hinglish": True, "chunk_schedule": [50, 100, 200]}
    put = await client.put("/admin/settings/voice-profile", json={"profile": good}, headers=ADMIN_HEADERS)
    assert put.status_code == 200 and put.json()["profile"] == good
    assert put.json()["effective"]["chunk_schedule"] == [50, 100, 200] and put.json()["effective"]["tone"] == "confident_informative"
    bad = await client.put("/admin/settings/voice-profile", json={"profile": {"chunk_schedule": [10]}}, headers=ADMIN_HEADERS)
    assert bad.status_code in (400, 422)
    nope = await client.put("/admin/settings/voice-profile", json={"profile": {"tone": "sarcastic"}}, headers=ADMIN_HEADERS)
    assert nope.status_code in (400, 422)


# --- one language per turn when the text arrives in pieces ------------------------------------------------------------------------


def _speak(chunks: list[str], **kw: Any) -> str:
    n = TTSNormalizer(enabled=True, **kw)
    return "".join(n.feed(c) for c in chunks) + n.flush()


def test_a_hinglish_turn_does_not_flip_to_english_number_words_on_a_clause_without_markers() -> None:
    out = _speak(["Kal 12 baje aapka appointment hai. ", "Total Rs 5,000 pay karna hoga."])
    assert "paanch hazaar rupaye" in out and "five thousand" not in out


def test_an_english_turn_stays_english() -> None:
    out = _speak(["Your appointment is at 3:30 pm. ", "The fee is Rs 5,000."])
    assert "five thousand rupees" in out and "paanch" not in out


def test_an_org_that_prefers_english_is_never_switched() -> None:
    out = _speak(["Kal aapka appointment hai. ", "Fee Rs 5,000 hai."], language="en")
    assert "five thousand rupees" in out


def test_the_language_choice_belongs_to_one_turn_and_one_normalizer() -> None:
    hindi, english = TTSNormalizer(enabled=True), TTSNormalizer(enabled=True)
    hindi.feed("Aapka kal appointment hai. ")
    assert english.feed("Fee is Rs 5,000. ") == "Fee is five thousand rupees. "  # no state shared across turns/calls
