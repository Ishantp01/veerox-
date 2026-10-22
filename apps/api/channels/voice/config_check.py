"""Checks the voice feature flags and settings for combinations that cannot work as intended.

Every voice behaviour change is a separate flag, so it is easy to switch on something whose
prerequisite is off (say TTS recovery with the OpenAI-native voice, where there is no ElevenLabs
socket to recover). Nothing here changes behaviour: it reports. It runs at startup (warnings go to
the log) and is available to an operator at ``GET /diag/voice-config`` before a flag is flipped.

``error``   - the configuration cannot work (calls would fail or be silent).
``warning`` - a flag will have no effect, or only partly, in this configuration.
``info``    - worth knowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from apps.api.config import settings as default_settings

Level = Literal["error", "warning", "info"]

# Every flag that changes what a call does or what is recorded, in the order they are meant to be enabled.
BEHAVIOUR_FLAGS = (
    "voice_latency_tracking",
    "voice_greeting_ai_disclosure",
    "voice_elevenlabs_prewarm",
    "voice_nonblocking_tts_finish",
    "voice_greeting_audio_cache",
    "voice_smart_turn_taking",
    "voice_safe_cancel",
    "voice_trim_interrupted_context",
    "voice_end_call_enabled",
    "voice_tts_recovery",
    "voice_reconnect_recovery",
    "voice_metering_v2",
    "voice_retention_worker_enabled",
    "voice_retention_delete_provider_audio",
    "log_privacy_enabled",
    "log_transcripts",
)
_ELEVENLABS_ONLY = (
    "voice_elevenlabs_prewarm",
    "voice_nonblocking_tts_finish",
    "voice_greeting_audio_cache",
    "voice_tts_recovery",
)


@dataclass(frozen=True)
class Issue:
    level: Level
    code: str
    message: str


def flag_snapshot(s: Any = None) -> dict[str, bool]:
    s = s or default_settings
    return {name: bool(getattr(s, name, False)) for name in BEHAVIOUR_FLAGS}


def check_voice_config(s: Any = None) -> list[Issue]:
    s = s or default_settings
    issues: list[Issue] = []

    def add(level: Level, code: str, message: str) -> None:
        issues.append(Issue(level, code, message))

    provider = getattr(s, "voice_tts_provider", "openai")
    if provider not in ("openai", "elevenlabs"):
        add("error", "tts_provider_unknown", f"VOICE_TTS_PROVIDER={provider!r} is not 'openai' or 'elevenlabs'.")
    if provider == "elevenlabs" and not getattr(s, "elevenlabs_api_key", None):
        add("error", "elevenlabs_key_missing", "VOICE_TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is not set: every call would be silent.")
    if provider != "elevenlabs":
        for name in _ELEVENLABS_ONLY:
            if getattr(s, name, False):
                add("warning", f"{name}_no_effect", f"{name.upper()} only acts on the ElevenLabs path; VOICE_TTS_PROVIDER={provider!r} ignores it.")
    if getattr(s, "voice_safe_cancel", False) and getattr(s, "voice_interruption_mode", "answer_now") == "finish_first":
        add("warning", "safe_cancel_finish_first", "VOICE_SAFE_CANCEL applies to the 'answer_now' interruption mode only; 'finish_first' still pauses and resumes.")
    if getattr(s, "voice_smart_turn_taking", False) and not getattr(s, "voice_live_turn_detection", True):
        add("warning", "smart_turns_without_live_detection", "VOICE_SMART_TURN_TAKING relies on live partial transcripts; VOICE_LIVE_TURN_DETECTION is off, so corrections/noise cannot be recognised.")
    if getattr(s, "voice_trim_interrupted_context", False) and not getattr(s, "voice_safe_cancel", False):
        add("info", "trim_without_safe_cancel", "VOICE_TRIM_INTERRUPTED_CONTEXT works with or without VOICE_SAFE_CANCEL, but the interrupted response will finish generating first.")
    if getattr(s, "voice_retention_delete_provider_audio", False) and not getattr(s, "voice_retention_worker_enabled", False):
        add("warning", "provider_delete_without_worker", "VOICE_RETENTION_DELETE_PROVIDER_AUDIO has no effect unless VOICE_RETENTION_WORKER_ENABLED is on.")
    if getattr(s, "voice_metering_v2", False) and float(getattr(s, "voice_usage_checkpoint_seconds", 60) or 0) < 15:
        add("info", "checkpoint_interval_clamped", "VOICE_USAGE_CHECKPOINT_SECONDS below 15 is raised to 15.")
    timeout = getattr(s, "voice_tts_first_audio_timeout_seconds", 6.0)
    if getattr(s, "voice_tts_recovery", False) and isinstance(timeout, (int, float)) and timeout < 3:
        add("warning", "tts_timeout_aggressive", f"VOICE_TTS_FIRST_AUDIO_TIMEOUT_SECONDS={timeout} is shorter than a slow first sentence can take; measure first-audio p99 before setting it this low.")
    if getattr(s, "voice_end_call_enabled", False) and float(getattr(s, "voice_end_call_max_wait_seconds", 8) or 0) < 2:
        add("warning", "end_call_wait_short", "VOICE_END_CALL_MAX_WAIT_SECONDS under 2 can cut a goodbye short.")
    if not getattr(s, "log_privacy_enabled", True):
        add("warning", "log_privacy_off", "LOG_PRIVACY_ENABLED=false: phone numbers and sensitive values appear in logs.")
    if "example.com" in str(getattr(s, "public_base_url", "")):
        add("error", "public_base_url_default", "PUBLIC_BASE_URL is still the example default: the telephony provider cannot reach this service.")
    if not getattr(s, "openai_api_key", None):
        add("info", "platform_openai_key_missing", "No platform OPENAI_API_KEY: only orgs with their own key can take calls.")

    # Webhook authentication (Phase 9). Same rule as channels/voice/signature.effective_mode, read from ``s``.
    configured = str(getattr(s, "plivo_signature_mode", "auto") or "auto").strip().lower()
    environment = str(getattr(s, "environment", "") or "").strip().lower()
    mode = configured if configured in ("off", "warn", "enforce") else ("enforce" if environment == "pilot" else "off")
    if mode == "off":
        add("warning", "webhooks_unauthenticated", "PLIVO_SIGNATURE_MODE resolves to off: /voice/answer and /voice/stream accept any caller. Roll out with 'warn', then 'enforce' (docs/production-release-9.md).")
    else:
        if getattr(s, "twilio_account_sid", None) and getattr(s, "twilio_auth_token", None):
            level: Level = "error" if mode == "enforce" else "warning"
            add(level, "signatures_without_twilio_support", "Signature checking understands Plivo only: with Twilio configured, Twilio's webhooks and media streams are refused (enforce) or reported (warn). Do not enforce while Twilio failover or inbound is in use.")
        if not getattr(s, "plivo_auth_token", None):
            add("warning", "signature_token_missing", "No PLIVO_AUTH_TOKEN: only orgs with their own Plivo token can pass the signature check.")
        base = str(getattr(s, "public_base_url", "") or "")
        if not base.startswith("https://") or base.rstrip("/").count("/") > 2:
            add("warning", "public_base_url_not_root_https", "Signatures are checked against PUBLIC_BASE_URL + the request path: it must be the https root the provider calls (no path prefix).")
    if not int(getattr(s, "voice_max_active_streams", 0) or 0):
        add("info", "no_stream_cap", "VOICE_MAX_ACTIVE_STREAMS is 0: the number of simultaneous calls on this process is not limited.")
    if not int(getattr(s, "voice_max_call_seconds", 0) or 0):
        add("info", "no_max_call_duration", "VOICE_MAX_CALL_SECONDS is 0: a call can run until the provider's own stream timeout.")
    if not getattr(s, "voice_hangup_on_stream_end", False):
        add("info", "leg_not_ended_on_stream_end", "VOICE_HANGUP_ON_STREAM_END is off: a restart leaves the phone line open (silent) until the caller hangs up.")
    return issues
