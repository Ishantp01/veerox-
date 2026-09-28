from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

DEPLOYMENT_MODES = ("owner", "client")


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

    # --- Multi-tenant licensing / client deployments ---------------------
    # One shared codebase, one config schema (this class), one .env.example
    # at the repo root — every deployment (the owner's own, and every
    # client's own separate server/database) sets the SAME variable names,
    # differing only in their values. `deployment_mode` is what tells a
    # given process which half of the schema below actually applies to it;
    # see `_validate_deployment_mode_requirements` at the bottom of this
    # class for the specific fields each mode requires.
    #
    # "owner": this is the central Veerox platform deployment — runs the
    # owner panel/API (routers/platform.py, billing.py's license endpoints)
    # and holds the source-of-truth org/license/feature data. "client": this
    # is a separate per-client deployment with its own database — it never
    # registers owner administration routes at all (see main.py's
    # create_app, not just a hidden frontend link) and instead validates its
    # licence directly against the owner over authenticated HTTPS on every
    # cache refresh (see core/license_cache.py) — there is no local
    # signature to verify, no signing keys anywhere in this schema.
    deployment_mode: str = "owner"
    # Client-mode only: where the owner API lives, and this deployment's own
    # bearer credential for it — issued once by
    # POST /platform/organizations/{id}/deployments (shown exactly once at
    # generation; the owner only ever stores its hash, see
    # db/models/client_deployment.py). Sent as `Authorization: Bearer
    # <token>` on every call to GET /platform/license and
    # GET|POST /platform/sync/* — never exposed to the frontend, browser
    # storage, URLs, or logs.
    license_api_url: str | None = None
    license_api_token: str | None = None

    @model_validator(mode="after")
    def _validate_deployment_mode_requirements(self) -> "Settings":
        """Fails startup fast rather than letting a misconfigured deployment
        come up half-working: a client deployment with no way to reach or
        authenticate to the owner would either crash on first use or, worse,
        run with no licence enforcement at all. The owner deployment itself
        needs nothing extra here — it validates itself via the existing
        platform-owner exemption (`deps.py::_org_is_platform_admin_owned`),
        not a client token."""
        if self.deployment_mode not in DEPLOYMENT_MODES:
            raise ValueError(
                f"DEPLOYMENT_MODE must be one of {DEPLOYMENT_MODES!r}, got {self.deployment_mode!r}"
            )

        if self.deployment_mode == "client":
            missing = [
                name
                for name, value in (
                    ("LICENSE_API_URL", self.license_api_url),
                    ("LICENSE_API_TOKEN", self.license_api_token),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "DEPLOYMENT_MODE=client requires "
                    f"{', '.join(missing)} to be set — see .env.example."
                )

        return self


settings = Settings()
