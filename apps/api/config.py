from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    # Resolve `.env` from the repository root so the API loads the same
    # settings no matter which working directory uvicorn is launched from.
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # App
    environment: str = "dev"
    log_level: str = "INFO"
    default_org_id: str = "00000000-0000-0000-0000-000000000001"
    # Required secret for the shared owner-org admin path. Keep it in the
    # repository root `.env` or the process environment; do not rely on a
    # placeholder fallback.
    admin_token: str

    # Dashboard login sessions (server-side, stored in Redis — see
    # apps/api/deps.py's get_current_user). Feature-flagged during rollout so
    # existing X-Admin-Token access keeps working until session auth is
    # verified end-to-end; see docs/plan for phase sequencing.
    session_ttl_seconds: int = 60 * 60 * 24 * 14
    require_session_auth: bool = True

    # Database / Redis
    database_url: str = "postgresql+asyncpg://veerox:veerox@localhost:5432/veerox"
    redis_url: str = "redis://localhost:6379/0"
    # SQLAlchemy's echo=True logs every statement synchronously — measured
    # adding multiple seconds per request locally (blocking console I/O, made
    # worse by non-ASCII AI-generated text tripping the stdlib logging
    # module's cp1252 encode fallback on every line). Opt-in only, never
    # tied to `environment` again.
    db_echo: bool = False
    startup_seed_required: bool = False
    test_database_url: str = "sqlite+aiosqlite:///:memory:"

    # OpenAI
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    openai_realtime_model: str = "gpt-4o-realtime-preview"
    openai_realtime_voice: str = "alloy"

    # Symmetric key (Fernet, see core/crypto.py) used to encrypt/decrypt
    # per-org secrets stored in the DB — currently just Org.
    # openai_api_key_encrypted, letting an org bring its own OpenAI key
    # instead of billing against the platform's. Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
    # and keep it in the environment only — never commit it, and never
    # rotate it without a re-encryption migration (existing ciphertext
    # becomes unreadable under a new key).
    secret_encryption_key: str | None = None

    # ElevenLabs — optional TTS swap for voice calls (see
    # channels/voice/elevenlabs_client.py). "openai" (default) keeps calls on
    # OpenAI Realtime's native speech-to-speech, unchanged. "elevenlabs"
    # switches the Realtime session to text-only output (still handles STT,
    # reasoning, tool-calling, turn-taking) and pipes the text through
    # ElevenLabs' streaming TTS instead — real latency/cost tradeoff, opt-in
    # only, never flip this without ELEVENLABS_API_KEY set.
    voice_tts_provider: str = "openai"
    # Live turn-taking: a second OpenAI connection streams partial transcripts of
    # the caller's speech so the agent can tell a filler ("haan", "ok") from a
    # real question while it is still talking (channels/voice/turn_controller.py).
    # Any failure falls back to the older duration-based rules; set false to
    # turn it off entirely (saves the extra connection per call).
    voice_live_turn_detection: bool = True
    # Register each org's Plivo numbers' inbound answer URL at every server
    # start (channels/voice/plivo_provisioning.py). Leave ON in production;
    # set PLIVO_STARTUP_REGISTRATION=false in any dev .env, otherwise starting
    # a dev server re-points the shared Plivo application at the dev ngrok URL.
    plivo_startup_registration: bool = True
    # Conversation Intelligence Engine (core/conversation/). The per-org
    # switch is Org.conversation_mode; these are global tuning/kill knobs.
    # Voice only ever runs the engine in log-only shadow mode — set
    # CONVERSATION_VOICE_SHADOW=false to stop the extra LLM calls entirely.
    conversation_voice_shadow: bool = True
    # Live voice decisions (Decision Layer). This is only the global ceiling:
    # a call is steered only when its org is ALSO set to voice mode "live"
    # (Org.conversation_voice_mode). CONVERSATION_VOICE_LIVE=false switches it
    # off everywhere without a deploy.
    conversation_voice_live: bool = True
    # How long the reply may wait for a decision before the call just
    # continues exactly as it does today, and the model-call timeout inside it.
    conversation_voice_decision_budget_seconds: float = 1.0
    conversation_voice_llm_timeout_seconds: float = 0.9
    # Silence handling on live-voice calls: after the agent finishes, this many
    # seconds of caller silence trigger a gentle "are you there?"; after
    # CONVERSATION_SILENCE_MAX_CHECKS of those the call enters the
    # connection-issue state. 0 switches silence handling off.
    conversation_silence_check_seconds: float = 8.0
    conversation_silence_max_checks: int = 2
    conversation_llm_timeout_seconds: float = 6.0
    conversation_min_confidence: float = 0.6
    # Phase 7: the mode NEW organizations start in (existing orgs are never
    # touched). "strict_script" keeps today's behaviour; set
    # CONVERSATION_DEFAULT_MODE=human_conversation (or smart_script) once the
    # results justify it. Voice stays "shadow" unless changed per org.
    conversation_default_mode: str = "strict_script"
    voice_live_transcribe_model: str = "gpt-live-transcribe"
    # gpt-live-transcribe streaming delay: minimal|low|medium|high|xhigh —
    # measured ~1s lower lag at "minimal" than the default.
    voice_live_transcribe_delay: str = "minimal"
    # What the agent does when the caller asks a real question while it is still
    # talking. "answer_now" (default): stop and answer straight away (small
    # acknowledgements like "ok" / "theek hai" are ignored either way).
    # "finish_first": pause, say it heard them, finish the point, read their
    # question back, then answer it.
    voice_interruption_mode: str = "answer_now"
    # Silence (ms) the Realtime VAD waits before deciding the caller has finished
    # speaking — the floor on how soon the agent can start answering. Lower =
    # faster replies, but a caller who pauses mid-sentence is cut off more.
    voice_vad_silence_ms: int = 300
    # Adaptive endpointing: hold the reply up to ~1s when the caller's text looks
    # unfinished ("haan, mera matlab..."). Off by default — it adds a second of
    # latency whenever a sentence happens to end on a connective word.
    voice_endpoint_hold: bool = False
    # Phase 1 voice-pipeline switches (channels/voice/). Instrumentation is
    # log-only and on by default; the two behaviour changes below are OFF so
    # existing calls run exactly as before until each is validated.
    voice_latency_tracking: bool = True
    # Open the next ElevenLabs socket when OpenAI starts a response, instead of
    # when its first text token arrives, so the connect overlaps the model's own
    # first-token time. A socket is never reused across turns.
    voice_elevenlabs_prewarm: bool = False
    # Finish an ElevenLabs turn (flush, drain audio, persist) in a tracked task
    # instead of inside the OpenAI event handler, so a caller who speaks during
    # the audio tail is processed immediately. False = the old inline await.
    voice_nonblocking_tts_finish: bool = False
    # Phase 3 interruption handling. Both OFF: calls cut and answer exactly as before.
    # True sends response.cancel for the response a real interruption cuts off
    # (instead of muting it and waiting for it to finish), drops every late event of a
    # cancelled response by id, persists the interrupted turn, and guards the call with a
    # watchdog + circuit breaker that fall back to mute-and-wait if the cancel misbehaves.
    voice_safe_cancel: bool = False
    # How long a cancel may go without its response.done before it counts as failed.
    voice_cancel_timeout_seconds: float = 2.0
    # True: corrections and non-speech noise are recognised when judging an overlap, an
    # unfinished sentence waits briefly for its end (same hold as VOICE_ENDPOINT_HOLD), and
    # the answer to an interruption is told to address it first without inventing facts.
    voice_smart_turn_taking: bool = False

    # Phase 5 - production reliability. Everything that changes what a call does is OFF until
    # it has been validated on a live call. Numeric knobs are clamped where they are used
    # (channels/voice/call_control.py::bounded), so a bad env value degrades to a safe default
    # instead of crashing startup or removing a safety bound.
    # Let the agent (or a caller's explicit request) end the call: wait, bounded, for the goodbye
    # to finish playing, then hang up through the telephony provider's API.
    voice_end_call_enabled: bool = False
    voice_end_call_max_wait_seconds: float = 8.0
    voice_end_call_grace_seconds: float = 0.8
    # Recover from ElevenLabs failures (connect error, silence, rejected settings): retry once with
    # safe settings, else play a pre-generated apology and end the call. Never silence.
    voice_tts_recovery: bool = False
    voice_tts_connect_timeout_seconds: float = 4.0
    voice_tts_first_audio_timeout_seconds: float = 6.0
    voice_tts_max_failures_per_call: int = 2
    # After an OpenAI Realtime reconnect: replay a few recent turns, tell the model what was already
    # done, never repeat a completed action, and end the call properly if reconnecting is hopeless.
    voice_reconnect_recovery: bool = False
    voice_reconnect_context_turns: int = 6
    voice_reconnect_budget_seconds: float = 45.0
    # Record measured provider usage (Realtime tokens, transcription seconds, TTS characters,
    # decision-layer tokens, previews) as UsageEvents. Measured only; no prices are invented.
    voice_metering_v2: bool = False
    # Spoken brand for NEW organisations (Org.voice_brand is set at creation). Existing orgs keep
    # voice_brand_name (their legacy brand) until someone sets their own.
    voice_new_org_brand: str = "Veerox"
    # Log hygiene: mask phone numbers and redact obvious sensitive values (OTPs, card/ID numbers,
    # emails) in log lines; log_transcripts=False also stops what callers/agents say being logged.
    log_privacy_enabled: bool = True
    log_transcripts: bool = True
    # Clear stored recording references past each org's configured retention (off until enabled).
    voice_retention_worker_enabled: bool = False
    # With the retention worker: also ask the telephony provider to delete the hosted audio, and clear
    # our stored reference only once the provider confirms. Off: only the reference is cleared.
    voice_retention_delete_provider_audio: bool = False
    # After an interruption, remove the part of the agent's answer the caller never heard from the
    # Realtime conversation (keeping only what was heard), so the model doesn't assume it said it.
    voice_trim_interrupted_context: bool = False
    # How often a call's measured usage is checkpointed to the ledger (seconds; clamped 15-600), so a
    # crash loses at most one interval of a call's usage instead of all of it. Needs VOICE_METERING_V2.
    voice_usage_checkpoint_seconds: float = 60.0
    # Spoken brand in the default greeting and the voice prompt example.
    voice_brand_name: str = "WorkAssign AI"
    # Greetings must identify the agent as an AI. True makes the default
    # greeting say so and rejects org greetings that don't; False restores the
    # previous wording exactly.
    voice_greeting_ai_disclosure: bool = True
    # Opt-in: pre-synthesize an org's custom greeting with ElevenLabs and play
    # the cached audio instead of generating it live (channels/voice/greeting.py).
    voice_greeting_audio_cache: bool = False
    # Voice-profile preview endpoint (POST /admin/settings/voice-profile/preview):
    # max syntheses per org per minute, and the longest phrase accepted.
    voice_preview_per_minute: int = 6
    voice_preview_max_chars: int = 300
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "CwhRBWXzGAHq8TQ4Fs17"
    # Low-latency model — eleven_multilingual_v2 sounds better but is too
    # slow for a live call; flash/turbo trade some quality for speed.
    elevenlabs_model_id: str = "eleven_flash_v2_5"

    # Plivo
    plivo_auth_id: str | None = None
    plivo_auth_token: str | None = None
    plivo_phone_number: str | None = None
    # Twilio — outbound-call failover only (see channels/voice/failover.py).
    # Unset by default; when absent, calling just runs on Plivo alone with no
    # backup, same as before this existed.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_phone_number: str | None = None
    # How many campaign calls the dialer will have in flight at once. Keep
    # below the Plivo account's concurrent-call cap (verified >= 6 by manual
    # test on 2026-07-16); defaults conservative since raising it costs
    # nothing but lowering it after overshooting means dropped/rejected calls.
    max_concurrent_calls: int = 50

    # Brevo transactional email (forgot-token delivery only, for now).
    brevo_api_key: str | None = None
    # Where the "Go to sign in" button in emails points.
    app_login_url: str = "https://app.workassignai.com/login"
    brevo_sender_email: str = "no-reply@veerox.ai"
    brevo_sender_name: str = "Veerox"

    # Meta WhatsApp Cloud API
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_whatsapp_business_account_id: str | None = None
    meta_verify_token: str | None = None
    meta_phone_number_id: str | None = None
    meta_access_token: str | None = None
    meta_graph_api_version: str = "v21.0"

    # Public base URL (used for Plivo XML <Stream url=...> and Meta webhook registration)
    public_base_url: str = "https://api.example.com"

    # CORS — comma-separated list of allowed frontend origins. This default
    # covers local dev only; set CORS_ALLOWED_ORIGINS in the environment
    # (e.g. Render) to the deployed dashboard origin(s) in production —
    # nothing production-specific is hardcoded here.
    cors_allowed_origins: str = (
        "http://localhost:3000,http://localhost:3001,"
        "http://127.0.0.1:3000,http://127.0.0.1:3001"
    )

    # Observability
    sentry_dsn: str | None = None

    # Usage metering / AWS cost allocation (see core/usage.py, workers/
    # aws_cost_importer.py, workers/cost_allocation_worker.py). Off by
    # default — the importer no-ops entirely until this is explicitly
    # enabled AND AWS credentials with Cost Explorer read access
    # (ce:GetCostAndUsage) are present in the environment (standard
    # AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_DEFAULT_REGION, or an
    # attached IAM role — boto3's normal credential chain, never a bespoke
    # one here). Never logged or exposed via any endpoint.
    aws_cost_import_enabled: bool = False
    aws_account_id: str | None = None
    # Flat per-org monthly platform fee added on top of allocated
    # infrastructure + third-party usage cost (req §16/§12) — a simple
    # documented default until/unless a per-plan fee schedule is needed.
    platform_fee_usd: str = "0"


settings = Settings()
