"""Natural-speech Phase 3: maximum-hold phrase flushing, the blind audio runner, and the settings display.

Every test is offline: any real ElevenLabs connection attempt fails the test (autouse guard), and the only socket
used is a fake. Nothing here is enabled by default."""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import random
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from apps.api.channels.voice import elevenlabs_client
from apps.api.channels.voice import voice_profile as vp
from apps.api.channels.voice.elevenlabs_client import PhraseBuffer
from apps.api.channels.voice.voice_profile import resolve_profile
from apps.api.routers import admin as admin_router
from apps.api.tests.test_elevenlabs_client import _FakeElevenLabsWS

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
AUDIO_B64 = "f39/f39/f39/"  # a few mu-law bytes, base64


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any real connection to ElevenLabs fails the test. Tests that need a socket install their own fake."""
    attempts: list[str] = []

    async def refuse(url: str, *a: object, **k: object) -> None:
        attempts.append(url)
        raise AssertionError("a test tried to open a real ElevenLabs connection")

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", refuse)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", None)
    # The pilot's platform voice/model, whatever environment the tests run in (tests may override).
    for module in (vp, elevenlabs_client):
        monkeypatch.setattr(module, "platform_voice_id", lambda: "PilotVoice12345")
        monkeypatch.setattr(module, "platform_model_id", lambda: "eleven_turbo_v2_5")
    return attempts


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _feed_words(buf: PhraseBuffer, text: str, clock: Clock | None = None, step: float = 0.0) -> list[str]:
    out = []
    for piece in text.replace(" ", " \0").split("\0"):
        if clock is not None:
            clock.now += step
        r = buf.feed(piece)
        if r:
            out.append(r)
    return out


# --- maximum hold: PhraseBuffer ---------------------------------------------------------------------------------


def test_without_a_maximum_hold_nothing_changes() -> None:
    buf = PhraseBuffer()
    assert _feed_words(buf, "bolna " * 40) == []  # unpunctuated text still waits for the end, as before
    assert buf.deadline() is None and buf.poll() == ""
    assert elevenlabs_client.PhraseBuffer().feed("जी बिल्कुल, आपका काम कल हो जाएगा।आगे बात करते हैं") == ""


def test_character_cap_releases_punctuation_free_text_at_a_space() -> None:
    buf = PhraseBuffer(max_hold_chars=100)
    text = "bolna " * 50  # 300 characters, no punctuation at all
    released = _feed_words(buf, text)
    assert released, "a long reply without punctuation must not wait for the end"
    assert 100 <= len(released[0]) < 110 and released[0].endswith(" ")
    assert "".join(released) + buf.flush() == text  # nothing lost, nothing repeated, never inside a word
    assert all(r.endswith(" ") for r in released)


def test_a_forced_release_prefers_a_comma_over_a_bare_space() -> None:
    buf = PhraseBuffer(max_hold_chars=60)
    text = "Dekhiye modular kitchen, pehle hum naap lete hain phir design banate hain aur"
    released = _feed_words(buf, text)
    assert released[0] == "Dekhiye modular kitchen, "  # cut at the comma, not at the last space before the cap
    assert "".join(released) + buf.flush() == text


def test_time_cap_releases_slow_text_when_the_next_word_arrives() -> None:
    clock = Clock()
    buf = PhraseBuffer(max_hold_seconds=0.8, clock=clock)
    assert buf.feed("Dekhiye modular kitchen mein ") == ""  # 29 chars, no boundary, not yet due
    assert buf.deadline() == pytest.approx(100.8)
    clock.now += 0.5
    assert buf.feed("pehle ") == ""
    clock.now += 0.4  # 0.9 s since the text started waiting
    assert buf.feed("naap") == "Dekhiye modular kitchen mein pehle "  # cut at the last space, "naap" is still arriving
    assert buf.flush() == "naap"
    assert buf.deadline() is None


def test_poll_releases_stalled_text_without_new_input_and_resets_the_clock() -> None:
    clock = Clock()
    buf = PhraseBuffer(max_hold_seconds=1.0, clock=clock)
    buf.feed("Dekhiye modular kitchen mein pehle ")
    assert buf.poll() == ""  # not due
    clock.now += 1.0
    assert buf.poll() == "Dekhiye modular kitchen mein pehle "
    assert buf.poll() == "" and buf.deadline() is None  # nothing left waiting


def test_short_acknowledgements_are_never_released_as_tiny_fragments() -> None:
    clock = Clock()
    buf = PhraseBuffer(max_hold_seconds=0.5, max_hold_chars=40, clock=clock)
    assert buf.feed("Haan ji. ") == ""  # 8 characters: below the 12-character minimum
    clock.now += 5.0
    assert buf.poll() == ""  # still held: releasing it would be a tiny fragment
    assert buf.feed("") == ""
    assert buf.flush() == "Haan ji. "  # spoken at the end of the reply, nothing lost
    # ... and when it is followed by a question it joins it, as before:
    assert PhraseBuffer(max_hold_chars=40).feed("Haan ji. Aapka ghar kitne BHK ka hai? Aur") == (
        "Haan ji. Aapka ghar kitne BHK ka hai? "
    )


def test_a_forced_cut_never_leaves_a_fragment_shorter_than_the_minimum() -> None:
    buf = PhraseBuffer(max_hold_chars=40)
    # The only space is early: cutting there would release a 4-character piece, so it waits instead.
    assert buf.feed("Haan " + "x" * 60) == ""
    assert buf.flush() == "Haan " + "x" * 60


def test_devanagari_danda_glued_to_the_next_word_is_a_sentence_end_when_a_hold_is_set() -> None:
    buf = PhraseBuffer(max_hold_chars=200)
    first = buf.feed("जी बिल्कुल, आपका काम कल हो जाएगा।आगे बात करते हैं")
    assert first == "जी बिल्कुल, आपका काम कल हो जाएगा। "  # a space is added so the words do not run together
    assert buf.flush() == "आगे बात करते हैं"
    # A danda followed by a space still works, and a danda too early in the text (a tiny fragment) does not cut.
    assert PhraseBuffer(max_hold_chars=200).feed("जी बिल्कुल, आपका काम कल हो जाएगा। आगे") == "जी बिल्कुल, आपका काम कल हो जाएगा। "
    assert PhraseBuffer(max_hold_chars=200).feed("हाँ।आगे बात") == ""


def test_numbers_abbreviations_and_normal_boundaries_keep_working_with_a_hold() -> None:
    buf = PhraseBuffer(max_hold_chars=200, max_hold_seconds=2.0)
    assert buf.feed("Plan 3.5 lakh ka hai aur isme sab kuch aa jaata hai. Theek?") == (
        "Plan 3.5 lakh ka hai aur isme sab kuch aa jaata hai. "
    )
    assert PhraseBuffer(max_hold_chars=200).feed("Aapka bill Rs. 1500 hai aur advance dena hoga. Theek") == (
        "Aapka bill Rs. 1500 hai aur advance dena hoga. "
    )


@pytest.mark.parametrize("seed", range(8))
def test_random_streams_never_lose_repeat_or_split_words(seed: int) -> None:
    rng = random.Random(seed)
    text = (
        "Dekhiye modular kitchen mein pehle hum aapke ghar ka naap lete hain, phir design finalize hota hai। "
        "Uske baad factory mein kaam shuru hota hai aur lagbhag teen hafte mein installation ho jaata hai. "
        "Haan ji? Theek hai... " + "bolna " * 30 + "आपका काम कल हो जाएगा।आगे बात करते हैं"
    )
    clock = Clock()
    buf = PhraseBuffer(max_hold_chars=90, max_hold_seconds=0.7, clock=clock)
    pieces, i = [], 0
    while i < len(text):  # token-sized deltas that may split inside a word
        n = rng.randint(1, 7)
        pieces.append(text[i : i + n])
        i += n
    released = []
    for d in pieces:
        clock.now += rng.choice([0.0, 0.05, 0.3, 1.0])
        r = buf.feed(d)
        if r:
            released.append(r)
        if rng.random() < 0.2:
            clock.now += 1.0
            p = buf.poll()
            if p:
                released.append(p)
    rest = buf.flush()
    assert "".join(released + [rest]).replace(" ", "") == text.replace(" ", "")  # only inserted spaces differ
    assert all(r[-1].isspace() for r in released)  # every release stops at a word boundary
    assert all(len(r.strip()) >= 12 for r in released)  # and none is a tiny fragment


# --- maximum hold: the session (fake socket) -------------------------------------------------------------------


def _fake_connect(monkeypatch: pytest.MonkeyPatch, messages: list[dict] | None = None) -> list[_FakeElevenLabsWS]:
    made: list[_FakeElevenLabsWS] = []

    async def connect(url: str) -> _FakeElevenLabsWS:
        made.append(_FakeElevenLabsWS(messages if messages is not None else [{"audio": AUDIO_B64, "isFinal": True}]))
        made[-1].url = url  # type: ignore[attr-defined]
        return made[-1]

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", connect)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    return made


def _texts(ws: _FakeElevenLabsWS) -> list[str]:
    return [str(m["text"]) for m in ws.sent[1:] if "text" in m]


@pytest.mark.asyncio
async def test_a_stalled_stream_is_flushed_by_the_hold_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _fake_connect(monkeypatch)
    voice = resolve_profile({"phrase_flush": True, "phrase_max_hold_ms": 200})
    async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
        await session.send_text("Dekhiye modular kitchen mein ")  # no punctuation, then the model stalls
        assert _texts(made[0]) == []
        await asyncio.sleep(0.6)  # three times the hold, with no new text
        assert _texts(made[0]) == ["Dekhiye modular kitchen mein "]
        assert made[0].sent[1] == {"text": "Dekhiye modular kitchen mein ", "flush": True}
        await session.send_text("pehle naap lete hain.")
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    assert "".join(_texts(made[0])).split() == "Dekhiye modular kitchen mein pehle naap lete hain.".split()
    assert session._hold_task is None
    assert session.chars_sent == len("Dekhiye modular kitchen mein pehle naap lete hain.")  # counted once each


@pytest.mark.asyncio
async def test_a_short_acknowledgement_is_not_flushed_by_the_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _fake_connect(monkeypatch)
    voice = resolve_profile({"phrase_flush": True, "phrase_max_hold_ms": 200})
    async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
        await session.send_text("Haan ji. ")
        await asyncio.sleep(0.5)
        assert _texts(made[0]) == []  # still waiting: a lone 8-character fragment would sound clipped
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    assert _texts(made[0]) == ["Haan ji. ", ""]


@pytest.mark.asyncio
async def test_without_a_hold_the_session_makes_no_timer_and_sends_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _fake_connect(monkeypatch)
    voice = resolve_profile({"phrase_flush": True})
    async with elevenlabs_client.ElevenLabsTTSSession(profile=voice) as session:
        assert session._hold_seconds is None
        for piece in ["Namaste, main ", "AI assistant hoon. ", "Kaise ", "help karun?"]:
            await session.send_text(piece)
        assert session._hold_task is None
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    assert made[0].sent[1] == {"text": "Namaste, main AI assistant hoon. ", "flush": True}
    assert made[0].sent[2] == {"text": "Kaise help karun? "}
    assert made[0].sent[3] == {"text": ""}


def test_hold_options_follow_the_org_and_need_phrase_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    assert resolve_profile({"phrase_flush": False, "phrase_max_hold_ms": 500}) is None  # contradictory: rejected
    ignored = elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile({"phrase_max_hold_ms": 500}))
    assert ignored._phrases is None and ignored._hold_seconds is None  # no flushing, so nothing to hold
    on = elevenlabs_client.ElevenLabsTTSSession(
        profile=resolve_profile({"phrase_flush": True, "phrase_max_hold_ms": 500, "phrase_max_hold_chars": 120})
    )
    assert on._hold_seconds == 0.5 and on._phrases is not None
    assert resolve_profile({"phrase_flush": True, "phrase_max_hold_ms": 10}) is None  # out of range
    assert resolve_profile({"phrase_flush": True, "phrase_max_hold_chars": 5000}) is None


def test_hold_options_default_off_change_no_fingerprint_and_do_not_leak_between_orgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    base = resolve_profile({"voice_id": "OrgAVoice12345", "phrase_flush": True})
    assert (base.phrase_max_hold_chars, base.phrase_max_hold_ms) == (None, None)
    held = resolve_profile({"voice_id": "OrgAVoice12345", "phrase_flush": True, "phrase_max_hold_ms": 800})
    assert held.fingerprint != base.fingerprint  # it changes how text is cut, so it changes the cached audio key
    a = elevenlabs_client.ElevenLabsTTSSession(profile=held)
    b = elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile({"voice_id": "OrgBVoice12345"}))
    c = elevenlabs_client.ElevenLabsTTSSession(profile=held)
    assert a._phrases is not None and b._phrases is None and c._phrases is not a._phrases
    assert a._send_lock is not c._send_lock


# --- settings display versus the real request (Task 3) --------------------------------------------------------


PROFILES = [
    None,
    {},
    {"model_id": "eleven_multilingual_v2"},
    {"model_id": "eleven_turbo_v2_5", "speed": 1.1},
    {"model_id": "eleven_multilingual_v2", "style": 0.4, "stability": 0.6, "use_speaker_boost": True},
    {"model_id": "eleven_flash_v2_5", "stability": 0.7},
    {"voice_id": "OrgAVoice12345"},
    {"phrase_flush": True, "phrase_max_hold_ms": 500, "natural_speech": True, "hindi_script": "roman"},
]
PLATFORMS = [
    ("PlatVoice123456", "eleven_flash_v2_5"),
    ("PlatVoice123456", "eleven_turbo_v2_5"),
    ("PlatVoice123456", "eleven_multilingual_v2"),  # production's configuration
]


async def _handshake(raw: dict | None, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, dict]:
    made = _fake_connect(monkeypatch)
    async with elevenlabs_client.ElevenLabsTTSSession(profile=resolve_profile(raw)) as session:
        await session.finish()
        _ = [c async for c in session.audio_chunks()]
    url = urlparse(made[0].url)  # type: ignore[attr-defined]
    return url.path.split("/")[-2], parse_qs(url.query)["model_id"][0], made[0].sent[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("raw", PROFILES)
async def test_displayed_effective_settings_equal_the_request_sent(
    raw: dict | None, platform: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    voice_id, model_id = platform
    for module in (vp, elevenlabs_client):
        monkeypatch.setattr(module, "platform_voice_id", lambda: voice_id)
        monkeypatch.setattr(module, "platform_model_id", lambda: model_id)
    shown = admin_router._voice_profile_out(SimpleNamespace(voice_profile=raw)).effective
    sent_voice, sent_model, handshake = await _handshake(raw, monkeypatch)
    assert shown["voice_id"] == sent_voice
    assert shown["model_id"] == sent_model
    assert shown["voice_settings"] == handshake["voice_settings"]


def test_production_effective_style_is_preserved_and_shown() -> None:
    """Production: multilingual v2 and no org profile. The call sends style 0.15 and the endpoint now says so."""
    golden = {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15}
    assert vp.default_voice_settings("eleven_multilingual_v2") == golden
    assert elevenlabs_client.ElevenLabsTTSSession(model_id="eleven_multilingual_v2")._voice_settings == golden
    assert vp.default_voice_settings("eleven_flash_v2_5") == {"stability": 0.45, "similarity_boost": 0.8}
    assert vp.default_voice_settings("eleven_turbo_v2_5") == {"stability": 0.45, "similarity_boost": 0.8}
    assert elevenlabs_client.ElevenLabsTTSSession(model_id="eleven_turbo_v2_5")._voice_settings == {
        "stability": 0.45, "similarity_boost": 0.8,
    }
    assert vp.default_voice_settings("eleven_flash_v2_5") is not vp.LEGACY_VOICE_SETTINGS  # a copy, never shared


def test_the_settings_endpoint_with_no_record_uses_the_platform_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vp, "platform_voice_id", lambda: "PlatVoice123456")
    monkeypatch.setattr(vp, "platform_model_id", lambda: "eleven_multilingual_v2")
    out = admin_router._voice_profile_out(None)
    assert out.profile is None
    assert out.effective["voice_settings"] == {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15}
    assert out.effective["model_id"] == "eleven_multilingual_v2" and out.effective["voice_id"] == "PlatVoice123456"
    assert out.effective["phrase_max_hold_ms"] is None and out.effective["natural_speech"] is False


# --- blind audio runner (Task 2) --------------------------------------------------------------------------------


def _runner():
    spec = importlib.util.spec_from_file_location("natural_speech_run", SCRIPTS / "natural_speech_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _ledger(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_default_run_is_offline_and_makes_zero_provider_requests(tmp_path: Path, no_real_provider: list[str]) -> None:
    runner = _runner()
    assert runner.main(["--out", str(tmp_path / "r")]) == 0
    assert no_real_provider == []  # no connection was even attempted
    assert not (tmp_path / "r" / "listening" / "clips").exists()  # no audio
    sheet = _rows(tmp_path / "r" / "listening" / "sheet.csv")
    assert len(sheet) == 78
    ledger = _ledger(tmp_path / "r" / "KEY" / "results.jsonl")
    assert len(ledger) == 78 and {r["status"] for r in ledger} == {"planned"}
    assert sum(r["chars_planned"] for r in ledger) == 6674


def test_the_sheet_reveals_nothing_about_the_configuration_and_the_key_is_separate(tmp_path: Path) -> None:
    runner = _runner()
    runner.main(["--out", str(tmp_path / "r")])
    sheet_text = (tmp_path / "r" / "listening" / "sheet.csv").read_text(encoding="utf-8")
    readme = (tmp_path / "r" / "listening" / "README.txt").read_text(encoding="utf-8")
    for secret in ("phrase_flush", "turbo", "multilingual", "flash", "baseline", "candidate", "experiment"):
        assert secret not in sheet_text and secret not in readme, secret
    key = json.loads((tmp_path / "r" / "KEY" / "key.json").read_text(encoding="utf-8"))
    assert len(key["jobs"]) == 78 and set(key["labels"]) == {"N0", "N1", "N2", "N3", "N4"}
    assert len(set(key["labels"].values())) == 5  # five distinct anonymous labels
    assert {j["clip_id"] for j in key["jobs"]} == {r["clip_id"] for r in _rows(tmp_path / "r" / "listening" / "sheet.csv")}
    assert sum(j["chars"] for j in key["jobs"]) == key["planned_chars"] == 6674
    assert key["estimated_billed_chars"] == 6674 + 2 * 78
    # every rating column asked for is present, blank
    assert {"indian_accent", "pronunciation", "rhythm", "intonation", "pauses", "naturalness"} <= set(_rows(
        tmp_path / "r" / "listening" / "sheet.csv")[0])
    assert all(r["indian_accent"] == "" and r["naturalness"] == "" for r in _rows(tmp_path / "r" / "listening" / "sheet.csv"))
    # the effective configuration is recorded in the key, for the analysis after rating
    n3 = next(j for j in key["jobs"] if j["candidate"] == "N3")
    assert n3["effective"]["voice_settings"]["style"] == 0.15 and n3["effective"]["model_id"] == "eleven_multilingual_v2"


def test_same_seed_same_plan_different_seed_different_labels(tmp_path: Path) -> None:
    runner = _runner()
    runner.main(["--out", str(tmp_path / "a"), "--seed", "7"])
    runner.main(["--out", str(tmp_path / "b"), "--seed", "7"])
    runner.main(["--out", str(tmp_path / "c"), "--seed", "8"])
    read = lambda d: (tmp_path / d / "listening" / "sheet.csv").read_text(encoding="utf-8")  # noqa: E731
    assert read("a") == read("b") and read("a") != read("c")


@pytest.mark.parametrize(
    "extra",
    [["--run"], ["--allow-provider-requests"], ["--run", "--allow-provider-requests"],
     ["--run", "--allow-provider-requests", "--max-chars", "4000"],
     ["--run", "--allow-provider-requests", "--max-chars", "7000"]],  # last: no API key configured
)
def test_a_paid_run_needs_every_opt_in_and_otherwise_sends_nothing(
    extra: list[str], tmp_path: Path, no_real_provider: list[str]
) -> None:
    code = _runner().main(["--out", str(tmp_path / "r"), *extra])
    assert code == 2
    assert no_real_provider == [] and not (tmp_path / "r" / "listening" / "clips").exists()


def test_a_paid_run_refuses_when_the_baseline_would_not_be_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_real_provider: list[str]
) -> None:
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", True)
    assert _runner().main(["--out", str(tmp_path / "r"), "--run", "--allow-provider-requests", "--max-chars", "7000"]) == 2
    assert no_real_provider == []


def test_the_whole_experiment_runs_against_a_fake_socket_and_counts_exact_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made = _fake_connect(monkeypatch)
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    code = _runner().main(["--out", str(tmp_path / "r"), "--run", "--allow-provider-requests",
                           "--max-chars", "7000", "--delta-ms", "0"])
    assert code == 0 and len(made) == 78
    final = {r["clip_id"]: r for r in _ledger(tmp_path / "r" / "KEY" / "results.jsonl")}
    assert len(final) == 78 and {r["status"] for r in final.values()} == {"ok"}
    assert all(r["chars_sent"] == r["chars_planned"] for r in final.values())  # exact, per job
    assert sum(r["chars_sent"] for r in final.values()) == 6674
    wavs = sorted(p.stem for p in (tmp_path / "r" / "listening" / "clips").glob("*.wav"))
    assert wavs == sorted(final)  # one clip per job, named only by its blind id


def test_a_failed_job_is_recorded_not_dropped_and_resume_retries_only_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    made: list[_FakeElevenLabsWS] = []

    async def flaky(url: str) -> _FakeElevenLabsWS:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("boom")  # a connection failure
        bad = calls["n"] == 5  # a provider error message
        made.append(_FakeElevenLabsWS([{"error": "voice_not_found"}] if bad else [{"audio": AUDIO_B64, "isFinal": True}]))
        return made[-1]

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", flaky)
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(elevenlabs_client.settings, "voice_tts_phrase_flush", False)
    args = ["--out", str(tmp_path / "r"), "--run", "--allow-provider-requests", "--max-chars", "7000", "--delta-ms", "0"]
    assert _runner().main(args) == 1  # not all jobs ok
    final = {r["clip_id"]: r for r in _ledger(tmp_path / "r" / "KEY" / "results.jsonl")}
    statuses = sorted(r["status"] for r in final.values())
    assert len(final) == 78 and statuses.count("failed") == 2 and statuses.count("ok") == 76  # none dropped
    codes = {r["error_code"] for r in final.values() if r["status"] == "failed"}
    assert codes == {"error_OSError", "voice_not_found"}
    assert len(list((tmp_path / "r" / "listening" / "clips").glob("*.wav"))) == 76

    calls["n"] = 100  # the provider recovers
    assert _runner().main([*args, "--resume"]) == 0
    final = {r["clip_id"]: r for r in _ledger(tmp_path / "r" / "KEY" / "results.jsonl")}
    assert {r["status"] for r in final.values()} == {"ok"}
    assert calls["n"] == 102  # only the two failed jobs were requested again
    assert len(list((tmp_path / "r" / "listening" / "clips").glob("*.wav"))) == 78


def test_offline_simulation_shows_a_bounded_wait_with_a_hold_and_says_it_is_only_buffering() -> None:
    """The simulation models OUR buffer only (never provider synthesis): with a hold the wait for punctuation-free
    text is bounded by the hold, without it the wait is the whole reply."""
    spec = importlib.util.spec_from_file_location("natural_speech_sheet", SCRIPTS / "natural_speech_sheet.py")
    sheet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sheet)
    text = " ".join(["bolna"] * 40)  # 240 characters, no punctuation
    _, without = sheet.first_send_seconds(text, 200)
    _, with_hold = sheet.first_send_seconds(text, 200, None, 100, 500)
    assert without == pytest.approx(len(text) / 200, abs=0.05)  # waits for the end of the reply
    assert with_hold <= 0.5 + 0.1 and with_hold < without  # bounded by the hold (plus one word)
    # ordinary punctuated text is unaffected by the hold
    b1, f1 = sheet.first_send_seconds("Namaste ji, aap kaise hain? Main theek hoon.", 200)
    b2, f2 = sheet.first_send_seconds("Namaste ji, aap kaise hain? Main theek hoon.", 200, None, 100, 500)
    assert f1 == f2
    assert "SIMULATED BUFFERING ONLY" in sheet.latency(sheet.load()[0])


# --- updated decisions: pilot baseline, maximum-hold candidate, analysis ------------------------------------------


def test_baseline_must_be_the_pilot_model_before_any_paid_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_real_provider: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(vp, "platform_model_id", lambda: "eleven_multilingual_v2")  # e.g. the dev .env
    monkeypatch.setattr(elevenlabs_client.settings, "elevenlabs_api_key", "sk_test")
    runner = _runner()
    assert runner.main(["--out", str(tmp_path / "a")]) == 0  # offline still works, with a warning
    assert "VEEROX_ENV_FILE=.env.pilot" in capsys.readouterr().err
    code = runner.main(["--out", str(tmp_path / "b"), "--run", "--allow-provider-requests", "--max-chars", "7000"])
    assert code == 2 and no_real_provider == []
    key = json.loads((tmp_path / "a" / "KEY" / "key.json").read_text(encoding="utf-8"))
    assert next(j for j in key["jobs"] if j["candidate"] == "N0")["effective"]["model_id"] == "eleven_multilingual_v2"


def test_plan_has_five_candidates_with_the_maximum_hold_candidate_and_the_pilot_baseline(tmp_path: Path) -> None:
    _runner().main(["--out", str(tmp_path / "r")])
    key = json.loads((tmp_path / "r" / "KEY" / "key.json").read_text(encoding="utf-8"))
    by = {}
    for j in key["jobs"]:
        by.setdefault(j["candidate"], j)
    assert by["N0"]["effective"]["model_id"] == "eleven_turbo_v2_5" and by["N0"]["profile"] == {}
    assert by["N3"]["effective"]["model_id"] == "eleven_multilingual_v2"
    assert by["N4"]["profile"] == {"phrase_flush": True, "phrase_max_hold_chars": 100, "phrase_max_hold_ms": 500}
    voice = resolve_profile(by["N4"]["profile"])
    assert voice is not None and voice.phrase_flush and (voice.phrase_max_hold_chars, voice.phrase_max_hold_ms) == (100, 500)
    # same voice everywhere, so only the one thing under test changes
    assert {j["effective"]["voice_id"] for j in key["jobs"]} == {"PilotVoice12345"}
    counts = {c: sum(1 for j in key["jobs"] if j["candidate"] == c) for c in ("N0", "N1", "N2", "N3", "N4")}
    assert counts == {"N0": 23 + 11, "N1": 11, "N2": 11, "N3": 11, "N4": 11}  # N0 is in E1 and E2; each other in one


def test_the_run_on_stress_sentence_has_no_sentence_punctuation_in_either_script() -> None:
    phrases = json.loads((Path(__file__).resolve().parents[3] / "docs" / "natural-speech-eval" / "phrases.json")
                         .read_text(encoding="utf-8"))["phrases"]
    run_on = [p for p in phrases if p["category"] == "run_on_stress"]
    assert {p["script"] for p in run_on} == {"roman", "devanagari"}
    for p in run_on:
        assert len(p["text"]) >= 190 and not any(ch in p["text"] for ch in ".?!।,;:…")


def _analysis_fixture() -> tuple[dict, list[dict], list[dict]]:
    mk = lambda cid, cand, exp, ph, take=1: {  # noqa: E731
        "clip_id": cid, "candidate": cand, "candidate_name": cand + " name", "experiment": exp, "phrase_id": ph, "take": take,
        "label": "L" + cand,
    }
    jobs = [mk("a1", "N0", "E2", "p1"), mk("a2", "N0", "E2", "p2"), mk("b1", "N4", "E2", "p1"), mk("b2", "N4", "E2", "p2"),
            mk("c1", "N0", "E1", "p1"), mk("d1", "N3", "E3", "p1")]
    key = {"labels": {"N0": "LN0", "N3": "LN3", "N4": "LN4"}, "jobs": jobs}
    lat = {"a1": 900, "a2": 1000, "b1": 1100, "b2": 1600, "c1": 5000, "d1": 1500}
    ledger = [{"clip_id": c, "status": "ok", "first_audio_ms": v} for c, v in lat.items()]
    ledger.append({"clip_id": "d1", "status": "failed", "first_audio_ms": None})  # the last line for a clip wins
    ledger.append({"clip_id": "d1", "status": "ok", "first_audio_ms": 1500})
    sheet = [
        {"clip_id": "a1", "indian_accent": "3", "pronunciation": "4", "naturalness": "3"},
        {"clip_id": "a2", "indian_accent": "5", "pronunciation": "x", "naturalness": ""},
        {"clip_id": "b1", "indian_accent": "4", "pronunciation": "5", "naturalness": "4"},
        {"clip_id": "zz", "indian_accent": "5"},
    ]
    return key, ledger, sheet


def _analyzer():
    spec = importlib.util.spec_from_file_location("natural_speech_analyze", SCRIPTS / "natural_speech_analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_analysis_reports_ratings_and_paired_latency_against_the_baseline() -> None:
    key, ledger, sheet = _analysis_fixture()
    an = _analyzer()
    res = an.analyze(key, ledger, sheet)
    n0, n4 = res["candidates"]["N0"], res["candidates"]["N4"]
    assert n0["ratings"]["indian_accent"] == {"mean": 4.0, "n": 2}  # 3 and 5
    assert n0["ratings"]["pronunciation"] == {"mean": 4.0, "n": 1}  # "x" is not a rating
    assert n0["ratings"]["naturalness"] == {"mean": 3.0, "n": 1}
    assert n4["ratings"]["indian_accent"]["mean"] == 4.0
    assert res["ignored_ratings"] == 1 and res["unknown_clips"] == 1
    # baseline latency for pairing uses the E2 clips (900, 1000), not the E1 clip (5000)
    assert n4["latency_vs_baseline"]["pairs"] == 2 and n4["latency_vs_baseline"]["median_ms"] == 400  # (+200, +600)
    assert n4["latency_vs_baseline"]["meets_median_target"] is False  # 400 > 300
    assert n4["latency_vs_baseline"]["meets_p95_target"] is False  # 600 > 500
    assert res["candidates"]["N3"]["latency_vs_baseline"] == {
        "pairs": 1, "median_ms": 600, "p95_ms": 600, "meets_median_target": False, "meets_p95_target": False,
    }
    text = an.render(res, key)
    assert "N4" in text and "indication, not a statistical result" in text and "MISSES" in text


def test_analysis_runs_from_files_and_makes_no_request(tmp_path: Path, no_real_provider: list[str],
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    key, ledger, sheet = _analysis_fixture()
    (tmp_path / "key.json").write_text(json.dumps(key), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text("\n".join(json.dumps(r) for r in ledger), encoding="utf-8")
    with (tmp_path / "sheet.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["clip_id", "indian_accent", "pronunciation", "naturalness"], extrasaction="ignore")
        w.writeheader()
        w.writerows(sheet)
    code = _analyzer().main(["--key", str(tmp_path / "key.json"), "--ledger", str(tmp_path / "results.jsonl"),
                             "--sheet", str(tmp_path / "sheet.csv")])
    assert code == 0 and no_real_provider == [] and "Measured first-audio latency" in capsys.readouterr().out
