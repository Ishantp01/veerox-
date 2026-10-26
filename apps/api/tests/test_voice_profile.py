"""Per-org voice profiles: validation, resolution, backward compatibility,
org isolation, greeting-cache keys, the speaking-style prompt option, the
admin endpoints and the preview endpoint."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import adapter, elevenlabs_client, voice_preview
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice.greeting import GreetingAudioCache
from apps.api.channels.voice.voice_profile import (
    ALLOWED_MODELS,
    LEGACY_VOICE_SETTINGS,
    VoiceProfileError,
    parse_profile,
    resolve_profile,
    speaking_style_enabled,
)
from apps.api.config import settings
from apps.api.core.prompts import VOICE_APPEND, render_voice_append
from apps.api.db.models import AccountUser, Org, OrgMembership
from apps.api.core.security import generate_login_token, hash_token
from apps.api.tests.test_admin_endpoints import (  # noqa: F401  (fixtures + helpers)
    ADMIN_HEADERS,
    ORG_ID,
    _login_as,
    _seed_org,
    client,
    fake_redis,
)


@pytest.fixture(autouse=True)
def _platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "PlatformVoice0001")
    monkeypatch.setattr(settings, "elevenlabs_model_id", "eleven_flash_v2_5")


# --- validation ------------------------------------------------------------------------------------


def test_valid_profile_parses() -> None:
    p = parse_profile(
        {"voice_id": "AbCdEfGh1234", "model_id": "eleven_turbo_v2_5", "speed": 1.1, "stability": 0.4}
    )
    assert p.model_id == "eleven_turbo_v2_5" and p.speed == 1.1


@pytest.mark.parametrize(
    "bad",
    [
        {"speed": 0.5},
        {"speed": 1.5},
        {"stability": 1.2},
        {"similarity_boost": -0.1},
        {"style": 2},
        {"model_id": "eleven_made_up"},
        {"voice_id": "bad id!"},
        {"voice_id": "short"},
        {"voice_id": "x" * 41},
        {"preferred_language": "fr"},
        {"language_code": "hi"},  # unsupported parameter: rejected, not ignored
        {"pronunciation_dictionary_locators": []},
        {"pronunciations": {"": "x"}},
        {"pronunciations": {"Veerox": ""}},
        {"pronunciations": {"a" * 41: "x"}},
        {"pronunciations": {f"w{i}": "x" for i in range(51)}},
    ],
)
def test_invalid_profiles_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(VoiceProfileError):
        parse_profile(bad)


def test_style_only_allowed_on_models_that_take_it() -> None:
    with pytest.raises(VoiceProfileError, match="style"):
        parse_profile({"style": 0.3})  # platform model is flash
    with pytest.raises(VoiceProfileError, match="style"):
        parse_profile({"style": 0.3, "model_id": "eleven_turbo_v2_5"})
    assert parse_profile({"style": 0.3, "model_id": "eleven_multilingual_v2"}).style == 0.3
    parse_profile({"style": 0})  # zero is the same as unset


def test_allow_list_has_the_comparison_models() -> None:
    assert {"eleven_turbo_v2_5", "eleven_flash_v2_5", "eleven_multilingual_v2"} <= set(ALLOWED_MODELS)


# --- resolution / backward compatibility ------------------------------------------------------------


def test_null_or_empty_profile_resolves_to_none() -> None:
    assert resolve_profile(None) is None
    assert resolve_profile({}) is None
    assert speaking_style_enabled(None) is False


def test_invalid_stored_profile_never_fails_a_call() -> None:
    assert resolve_profile({"speed": 99}) is None


def test_profile_merges_over_platform_settings() -> None:
    r = resolve_profile({"speed": 1.1})
    assert r is not None
    assert r.voice_id == "PlatformVoice0001" and r.model_id == "eleven_flash_v2_5"
    assert r.voice_settings == {"stability": 0.36, "similarity_boost": 0.75, "speed": 1.1}
    r2 = resolve_profile(
        {"voice_id": "OrgVoice123456", "model_id": "eleven_multilingual_v2", "style": 0.2,
         "stability": 0.3, "similarity_boost": 0.9, "use_speaker_boost": True}
    )
    assert r2 is not None and r2.voice_id == "OrgVoice123456"
    assert r2.voice_settings == {
        "stability": 0.3, "similarity_boost": 0.9, "style": 0.2, "use_speaker_boost": True,
    }


def test_style_is_dropped_if_platform_model_later_cannot_take_it() -> None:
    r = resolve_profile({"model_id": "eleven_multilingual_v2", "style": 0.4})
    assert r is not None and "style" in r.voice_settings


class _RawWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def close(self) -> None: ...

    def __aiter__(self) -> "_RawWS":
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


async def _handshake(monkeypatch: pytest.MonkeyPatch, **session_kw: Any) -> tuple[str, str]:
    ws = _RawWS()
    urls: list[str] = []

    async def fake_connect(url: str) -> _RawWS:
        urls.append(url)
        return ws

    monkeypatch.setattr(elevenlabs_client.websockets, "connect", fake_connect)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    async with elevenlabs_client.ElevenLabsTTSSession(**session_kw):
        pass
    return urls[0], ws.sent[0]


async def test_no_profile_sends_the_exact_legacy_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    url, first = await _handshake(monkeypatch)
    assert url == (
        "wss://api.elevenlabs.io/v1/text-to-speech/PlatformVoice0001/stream-input"
        "?model_id=eleven_flash_v2_5&output_format=ulaw_8000"
    )
    assert first == json.dumps(
        {"text": " ", "voice_settings": {"stability": 0.36, "similarity_boost": 0.75}, "xi_api_key": "sk_test"}
    )
    assert LEGACY_VOICE_SETTINGS == {"stability": 0.36, "similarity_boost": 0.75}


async def test_profile_changes_voice_model_and_settings_in_the_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = resolve_profile(
        {"voice_id": "OrgVoice123456", "model_id": "eleven_turbo_v2_5", "speed": 0.9, "stability": 0.7}
    )
    url, first = await _handshake(monkeypatch, profile=profile)
    assert "/OrgVoice123456/stream-input" in url and "model_id=eleven_turbo_v2_5" in url
    assert json.loads(first)["voice_settings"] == {
        "stability": 0.7, "similarity_boost": 0.75, "speed": 0.9,
    }


# --- per-call, per-org -------------------------------------------------------------------------------


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...


async def test_live_session_uses_the_calls_own_profile_only(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[dict[str, Any]] = []

    class Fake:
        def __init__(self, **kw: Any) -> None:
            made.append(kw)
            self.connect_ms = 1

        async def __aenter__(self) -> "Fake":
            return self

    monkeypatch.setattr(elevenlabs_client, "ElevenLabsTTSSession", Fake)
    voice_a = resolve_profile({"voice_id": "OrgAVoice12345"})
    voice_b = resolve_profile({"voice_id": "OrgBVoice12345"})
    a = adapter.CallState(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), voice=voice_a)
    b = adapter.CallState(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), voice=voice_b)
    plain = adapter.CallState(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=uuid.uuid4())
    await adapter._open_elevenlabs_session(a, _Log())
    await adapter._open_elevenlabs_session(b, _Log())
    await adapter._open_elevenlabs_session(plain, _Log())
    assert made == [{"profile": voice_a}, {"profile": voice_b}, {}]  # no profile => constructed exactly as before


async def test_load_org_voice_is_resolved_from_that_orgs_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_a, org_b, org_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            Org(id=org_a, name="A", voice_profile={"voice_id": "OrgAVoice12345"}),
            Org(id=org_b, name="B", voice_profile={"voice_id": "OrgBVoice12345", "speed": 1.2}),
            Org(id=org_c, name="C"),
        ]
    )
    await db_session.commit()

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Ctx())
    va = await bridge._load_org_voice(org_a)
    vb = await bridge._load_org_voice(org_b)
    vc = await bridge._load_org_voice(org_c)
    assert va is not None and va.voice_id == "OrgAVoice12345" and "speed" not in va.voice_settings
    assert vb is not None and vb.voice_id == "OrgBVoice12345" and vb.voice_settings["speed"] == 1.2
    assert vc is None  # NULL profile -> unchanged platform behaviour
    assert await bridge._load_org_voice(uuid.uuid4()) is None  # unknown org


# --- greeting audio cache ---------------------------------------------------------------------------


async def test_cache_key_depends_on_org_and_every_voice_setting() -> None:
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    text = "Namaste, main Acme ka AI assistant hoon."
    base = resolve_profile({"voice_id": "OrgAVoice12345"})
    same = resolve_profile({"voice_id": "OrgAVoice12345"})
    keys = {
        "legacy": GreetingAudioCache.key(org_a, text),
        "base": GreetingAudioCache.key(org_a, text, base),
        "other_org": GreetingAudioCache.key(org_b, text, base),
        "voice": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgBVoice12345"})),
        "model": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgAVoice12345", "model_id": "eleven_turbo_v2_5"})),
        "speed": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgAVoice12345", "speed": 1.1})),
        "stability": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgAVoice12345", "stability": 0.9})),
        "normalize": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgAVoice12345", "normalize_speech": True})),
        "pron": GreetingAudioCache.key(org_a, text, resolve_profile({"voice_id": "OrgAVoice12345", "pronunciations": {"Acme": "Akme"}})),
    }
    assert len(set(keys.values())) == len(keys)
    assert GreetingAudioCache.key(org_a, text, same) == keys["base"]


async def test_cache_never_serves_one_orgs_or_one_profiles_audio_to_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "voice_greeting_audio_cache", True)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    calls: list[tuple[str, str | None]] = []

    async def synth(text: str, voice: Any = None) -> list[str]:
        calls.append((text, voice.voice_id if voice else None))
        return [f"audio-{voice.voice_id if voice else 'platform'}"]

    import asyncio

    cache = GreetingAudioCache(synth=synth)
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    va = resolve_profile({"voice_id": "OrgAVoice12345"})
    vb = resolve_profile({"voice_id": "OrgBVoice12345"})
    cache.ensure_in_background(org_a, "hi AI", va)
    cache.ensure_in_background(org_b, "hi AI", vb)
    cache.ensure_in_background(org_b, "hi AI")
    await asyncio.sleep(0.05)
    assert cache.get(org_a, "hi AI", va).chunks == ("audio-OrgAVoice12345",)  # type: ignore[union-attr]
    assert cache.get(org_b, "hi AI", vb).chunks == ("audio-OrgBVoice12345",)  # type: ignore[union-attr]
    assert cache.get(org_b, "hi AI").chunks == ("audio-platform",)  # type: ignore[union-attr]
    assert cache.get(org_a, "hi AI", vb) is None  # org A's text under org B's voice: miss
    assert cache.get(org_a, "hi AI") is None
    assert len(calls) == 3


# --- speaking-style prompt option -----------------------------------------------------------------------


def test_prompt_is_byte_identical_when_style_is_off() -> None:
    legacy_default = render_voice_append("WorkAssign AI", ai_disclosure=True)
    assert render_voice_append("WorkAssign AI", ai_disclosure=True, speaking_style=False) == legacy_default
    assert VOICE_APPEND == legacy_default
    off = render_voice_append("Acme", ai_disclosure=False, speaking_style=False)
    assert off == render_voice_append("Acme", ai_disclosure=False)


def test_style_on_only_appends_guidance() -> None:
    off = render_voice_append("Acme")
    on = render_voice_append("Acme", speaking_style=True)
    assert on.startswith(off.rstrip("\n"))
    assert "Speaking style" in on and "Speaking style" not in off
    assert "add pauses or delays just to sound human" in on


@pytest.fixture
def reuse_db(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Ctx())
    return db_session


async def test_system_instructions_follow_the_calls_org_only(reuse_db: AsyncSession) -> None:
    on_org, off_org = uuid.uuid4(), uuid.uuid4()
    reuse_db.add_all(
        [
            Org(id=on_org, name="Styled", voice_profile={"speaking_style": True}),
            Org(id=off_org, name="Plain", voice_profile={"speed": 1.1}),
        ]
    )
    await reuse_db.commit()
    styled = await bridge._system_instructions(None, on_org)
    plain = await bridge._system_instructions(None, off_org)
    assert "Speaking style (human-like delivery)" in styled
    assert "Speaking style (human-like delivery)" not in plain


# --- admin endpoints -----------------------------------------------------------------------------------


async def test_profile_defaults_to_none_with_platform_effective(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    body = (await client.get("/admin/settings/voice-profile", headers=ADMIN_HEADERS)).json()
    assert body["profile"] is None
    assert body["effective"]["voice_id"] == "PlatformVoice0001"
    assert body["effective"]["voice_settings"] == {"stability": 0.36, "similarity_boost": 0.75}
    assert "eleven_turbo_v2_5" in body["allowed_models"]


async def test_profile_can_be_saved_read_back_and_cleared(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await _seed_org(db_session)
    put = await client.put(
        "/admin/settings/voice-profile",
        json={"profile": {"model_id": "eleven_turbo_v2_5", "speed": 1.1, "normalize_speech": True}},
        headers=ADMIN_HEADERS,
    )
    assert put.status_code == 200
    assert put.json()["profile"] == {"model_id": "eleven_turbo_v2_5", "speed": 1.1, "normalize_speech": True}
    assert put.json()["effective"]["voice_settings"]["speed"] == 1.1
    await db_session.refresh(org)
    assert org.voice_profile["model_id"] == "eleven_turbo_v2_5"

    cleared = await client.put("/admin/settings/voice-profile", json={"profile": None}, headers=ADMIN_HEADERS)
    assert cleared.json()["profile"] is None
    await db_session.refresh(org)
    assert org.voice_profile is None


@pytest.mark.parametrize(
    "bad",
    [
        {"speed": 3},
        {"model_id": "gpt-voice"},
        {"voice_id": "no spaces allowed"},
        {"language_code": "hi"},
        {"style": 0.5},  # flash (platform model) can't take style
    ],
)
async def test_invalid_profile_is_422_and_not_saved(
    client: AsyncClient, db_session: AsyncSession, bad: dict[str, Any]
) -> None:
    org = await _seed_org(db_session)
    response = await client.put("/admin/settings/voice-profile", json={"profile": bad}, headers=ADMIN_HEADERS)
    assert response.status_code == 422
    await db_session.refresh(org)
    assert org.voice_profile is None


async def test_profile_endpoints_need_auth_and_org_admin(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    from apps.api.tests.test_admin_endpoints import _require_session_auth

    with _require_session_auth(True):
        assert (await client.get("/admin/settings/voice-profile")).status_code in (401, 403)
        member = await _login_as(client, db_session, email="m@voice.test", role="member")
        for method, path, kw in (
            ("get", "/admin/settings/voice-profile", {}),
            ("put", "/admin/settings/voice-profile", {"json": {"profile": {"speed": 1.0}}}),
            ("post", "/admin/settings/voice-profile/preview", {"json": {"text": "hi"}}),
            ("get", "/admin/settings/voice-profile/phrases", {}),
        ):
            r = await getattr(client, method)(path, headers=member, **kw)
            assert r.status_code == 403, path


async def test_one_orgs_profile_is_invisible_to_another_org(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from apps.api.tests.test_admin_endpoints import _require_session_auth

    org_b = uuid.uuid4()
    db_session.add(Org(id=ORG_ID, name="A", voice_profile={"voice_id": "OrgAVoice12345"}))
    db_session.add(Org(id=org_b, name="B"))
    token = generate_login_token()
    account = AccountUser(email="admin-b@voice.test", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org_b, account_user_id=account.id, role="admin"))
    await db_session.commit()
    with _require_session_auth(True):
        login = await client.post("/auth/login", json={"token": token})
        headers = {"X-Session-Token": login.json()["token"]}
        got = await client.get("/admin/settings/voice-profile", headers=headers)
        assert got.status_code == 200
        assert got.json()["profile"] is None  # org B sees org B, never org A's
        assert got.json()["effective"]["voice_id"] == "PlatformVoice0001"
        await client.put(
            "/admin/settings/voice-profile", json={"profile": {"speed": 1.2}}, headers=headers
        )
    a = await db_session.get(Org, ORG_ID)
    await db_session.refresh(a)
    assert a.voice_profile == {"voice_id": "OrgAVoice12345"}  # org A untouched by B's save


# --- preview ---------------------------------------------------------------------------------------------


def test_phrase_set_covers_every_required_category() -> None:
    phrases = voice_preview.load_phrases()
    cats = {p["category"] for p in phrases}
    assert {"hindi", "english", "hinglish", "indian_names", "rupee_amounts", "dates_times",
            "questions", "conversational"} <= cats
    assert len({p["id"] for p in phrases}) == len(phrases)


def test_wav_wrapper_is_a_valid_mulaw_container() -> None:
    wav = voice_preview.ulaw_wav(b"\xff" * 101)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert int.from_bytes(wav[4:8], "little") == len(wav) - 8
    assert int.from_bytes(wav[20:22], "little") == 7  # mu-law format tag


def test_rate_limiter_is_per_org() -> None:
    lim = voice_preview.RateLimiter()
    a, b = uuid.uuid4(), uuid.uuid4()
    assert [lim.allow(a, 2, now=0.0), lim.allow(a, 2, now=1.0), lim.allow(a, 2, now=2.0)] == [True, True, False]
    assert lim.allow(b, 2, now=2.0) is True
    assert lim.allow(a, 2, now=61.0) is True


async def test_preview_fails_clearly_without_an_api_key(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    monkeypatch.setattr(settings, "elevenlabs_api_key", None)
    r = await client.post("/admin/settings/voice-profile/preview", json={"text": "hello"}, headers=ADMIN_HEADERS)
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


async def test_preview_validates_text_phrase_and_candidate(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    post = lambda body: client.post("/admin/settings/voice-profile/preview", json=body, headers=ADMIN_HEADERS)  # noqa: E731
    assert (await post({})).status_code == 422
    assert (await post({"text": "x" * (settings.voice_preview_max_chars + 1)})).status_code == 422
    assert (await post({"phrase_id": "nope"})).status_code == 422
    assert (await post({"text": "hi", "profile": {"speed": 9}})).status_code == 422


async def test_preview_synthesizes_with_the_saved_or_candidate_profile(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await _seed_org(db_session)
    org.voice_profile = {"voice_id": "OrgAVoice12345"}
    await db_session.commit()
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    seen: list[Any] = []

    async def fake_synth(text: str, voice: Any = None, **_: Any) -> voice_preview.SynthResult:
        seen.append((text, voice))
        return voice_preview.SynthResult(audio=b"\xff" * 80, connect_ms=5, first_audio_ms=123, total_ms=200)

    monkeypatch.setattr(voice_preview, "synthesize", fake_synth)
    saved = await client.post("/admin/settings/voice-profile/preview", json={"phrase_id": "hi_greeting"}, headers=ADMIN_HEADERS)
    assert saved.status_code == 200 and saved.headers["content-type"] == "audio/wav"
    assert saved.headers["x-voice-first-audio-ms"] == "123" and saved.content[:4] == b"RIFF"
    assert seen[0][1].voice_id == "OrgAVoice12345"

    cand = await client.post(
        "/admin/settings/voice-profile/preview",
        json={"text": "hello", "profile": {"voice_id": "CandVoice12345"}},
        headers=ADMIN_HEADERS,
    )
    assert cand.status_code == 200 and seen[1][1].voice_id == "CandVoice12345"
    await db_session.refresh(org)
    assert org.voice_profile == {"voice_id": "OrgAVoice12345"}  # preview never saves


async def test_preview_is_rate_limited(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.setattr(settings, "voice_preview_per_minute", 1)
    monkeypatch.setattr(voice_preview, "preview_limiter", voice_preview.RateLimiter())

    async def fake_synth(text: str, voice: Any = None, **_: Any) -> voice_preview.SynthResult:
        return voice_preview.SynthResult(audio=b"\xff", connect_ms=1, first_audio_ms=1, total_ms=1)

    monkeypatch.setattr(voice_preview, "synthesize", fake_synth)
    first = await client.post("/admin/settings/voice-profile/preview", json={"text": "a"}, headers=ADMIN_HEADERS)
    second = await client.post("/admin/settings/voice-profile/preview", json={"text": "a"}, headers=ADMIN_HEADERS)
    assert (first.status_code, second.status_code) == (200, 429)


async def test_synthesize_without_key_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", None)
    with pytest.raises(voice_preview.PreviewUnavailable):
        await voice_preview.synthesize("hello")
