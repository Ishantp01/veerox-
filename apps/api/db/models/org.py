from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db.base import Base
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.models.script import Script

# active = normal access. suspended = manually locked out by the platform
# admin regardless of expiry. expired = license_expires_at has passed —
# set by workers/license_expiry_worker.py, not chosen directly by an admin
# action (see routers/billing.py's license endpoints).
ORG_LICENSE_STATUSES = ("active", "suspended", "expired")

# Fixed set of togglable product modules — a platform admin can restrict an
# org to a subset of these via Org.enabled_features (see deps.py's
# require_feature). Each key maps 1:1 to one of these routers' prefix/tag.
# Not user-extensible; unknown keys are rejected by the admin-facing schemas
# (schemas/billing.py's OrgUpdateIn, schemas/auth.py's ProvisionOrgIn).
AVAILABLE_ORG_FEATURES = (
    "crm",
    "appointments",
    "follow_ups",
    "sales",
    "helpdesk",
    "tickets",
    "templates",
    "conversations",
)


def validate_org_features(features: list[str] | None) -> list[str] | None:
    """Shared pydantic field_validator body for schemas/billing.py's
    OrgUpdateIn and schemas/auth.py's ProvisionOrgIn — rejects any key
    outside AVAILABLE_ORG_FEATURES."""
    if features is None:
        return None
    unknown = sorted(set(features) - set(AVAILABLE_ORG_FEATURES))
    if unknown:
        raise ValueError(f"Unknown feature(s): {', '.join(unknown)}")
    return features


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Manually admin-managed license — replaces the old Razorpay-based
    # billing_status/Plan system entirely (see docs/razorpay-billing-removal.md).
    # deps.py's enforce_org_license blocks every org-scoped request once this
    # is "suspended" or "expired".
    license_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="active"
    )
    license_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    license_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The number of days the license was last issued/renewed for — remembered
    # so a quick one-click renew (no form) can reuse the same duration the
    # admin used last time instead of a hardcoded default (see
    # routers/billing.py's renew_license / QuickRenewButton in the frontend).
    # Untouched by extend (additive on top of the existing expiry, not a
    # fresh duration) and by suspend/reactivate.
    license_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-text admin note (e.g. "renewed via bank transfer 2026-09-16") —
    # shown on the Organizations page, purely informational.
    license_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Platform-admin-set restriction on which product modules this org can
    # use — keys from AVAILABLE_ORG_FEATURES above, enforced per-request by
    # deps.py's require_feature on each gated router. NULL (the default for
    # every org today, old and new) means "unrestricted" — distinct from an
    # explicit `[]`, which means the admin has disabled every module. The
    # platform's own operating org is always exempt regardless of this value
    # (see deps.py's _org_is_platform_admin_owned).
    enabled_features: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Platform-admin-set cap on how many OrgMembership rows this org may
    # have — enforced in routers/team.py's invite_member. NULL = unlimited
    # (unchanged default behavior for every org today).
    max_team_members: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Org-authored replacement for the built-in OUTBOUND_CALL_PROMPT (see
    # core/prompts.py) — NULL means "use the platform default script".
    # WhatsApp's last-resort fallback these days (core/agent.py::
    # _system_prompt_for), used only when the org has no WhatsApp `Script`
    # row of its own either — both channels otherwise pick from the
    # `scripts` relationship below (see channels/voice/realtime_bridge.py::
    # _system_instructions for voice), one org-wide default per channel plus
    # per-campaign overrides (db/models/call_campaign.py's script_id /
    # whatsapp_script_id). Kept here, unmigrated, purely as that final
    # WhatsApp fallback.
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # This org's dedicated Plivo/Twilio/WhatsApp numbers — an org can have
    # several of each (see db/models/org_phone_number.py). Plivo/Twilio's
    # answer webhook passes the dialed number as `To`, letting
    # channels/voice/webhook.py resolve the org for an *inbound* call on any
    # of them; outbound calls round-robin across every row per provider, in
    # `position` order (see channels/voice/org_numbers.py::
    # get_rotating_numbers) — ordered the same way here so the settings page
    # lists numbers in the order they dial. WhatsApp rows instead carry
    # Meta's `phone_number_id`: every inbound webhook payload carries it, so
    # it's how channels/whatsapp/adapter.py tells which org a message
    # belongs to instead of hardcoding settings.default_org_id — no
    # matching row means "not yet provisioned with a dedicated number",
    # falling back to settings.default_org_id.
    phone_numbers: Mapped[list["OrgPhoneNumber"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="OrgPhoneNumber.position"
    )
    # This org's AI script library (see db/models/script.py), split by
    # channel. Exactly one row per channel is expected to carry
    # is_default=True; that's the base a campaign falls back to when it has
    # no script_id/whatsapp_script_id of its own.
    scripts: Mapped[list["Script"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    # Explicit override of failover.py's automatic Plivo-first/Twilio-
    # fallback ordering — "plivo", "twilio", or NULL (automatic, the
    # default: prefer whichever provider this org has a dedicated number
    # on, Plivo if both/neither). Applied at every outbound-calling entry
    # point (single admin call, AI callback, campaign dialer, follow-up
    # dispatcher) via `initiate_call`'s `preferred_provider` kwarg — see
    # routers/admin.py's PUT /admin/settings/calling.
    preferred_voice_provider: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Dialing prefix (e.g. "+91") set when the org is created. Any phone
    # number a user types/uploads without an international prefix gets this
    # prepended — see core/phone.py::normalize_phone.
    default_country_code: Mapped[str] = mapped_column(
        String(6), nullable=False, server_default="+91"
    )
    # Which approved WhatsApp template the human-handoff notification sends
    # (core/tools.py::transfer_to_human), chosen on the /whatsapp/settings
    # page. The template must have exactly two body variables: {{1}} the
    # caller's number, {{2}} the escalation reason. NULL = built-in default
    # (the hardcoded `agent_connect_request` once Meta-approved, otherwise
    # the `appointment_confirmation` fallback). Set via PUT /admin/settings/whatsapp.
    agent_connect_template_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # This org's own OpenAI API key (Fernet-encrypted, see core/crypto.py),
    # letting it bring/pay for its own usage instead of the platform's
    # shared settings.openai_api_key. NULL = not configured, fall back to
    # the platform key everywhere a key is resolved (core/org_openai_key.py
    # ::resolve_openai_api_key). Never returned to the client in plaintext —
    # routers/admin.py's openai-key endpoints only ever expose a masked
    # preview (core/crypto.py::mask_secret) plus a configured boolean.
    openai_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # This org's own Plivo/Twilio/Meta WhatsApp channel credentials
    # (Fernet-encrypted secrets, see core/crypto.py), letting each client
    # bring its own provider account instead of dialing/sending through the
    # platform's shared settings.plivo_*/twilio_*/meta_* credentials. There
    # is deliberately NO fallback to the platform credentials once this
    # feature is live — see core/org_credentials.py::resolve_plivo_credentials
    # / resolve_twilio_credentials / resolve_meta_credentials, which every
    # outbound call/SMS/WhatsApp send site now goes through. An org's own
    # dedicated phone numbers still live separately in OrgPhoneNumber
    # (db/models/org_phone_number.py) — these columns are only the account-
    # level auth needed to place calls/sends on THAT org's provider account.
    # Non-secret ids are stored in plaintext; secrets are Fernet-encrypted
    # and masked (core/crypto.py::mask_secret) whenever shown back to the
    # client, same pattern as openai_api_key_encrypted above.
    plivo_auth_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plivo_auth_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    twilio_account_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    twilio_auth_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_app_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_app_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_whatsapp_business_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # This org's own Meta webhook verify token — Meta webhooks are
    # configured per-App in the Meta dashboard, not per phone number, so an
    # org bringing its own Meta App must register OUR shared callback URL
    # ({PUBLIC_BASE_URL}/webhook/whatsapp) in THEIR App's dashboard with
    # THIS token. channels/whatsapp/webhook.py's GET handshake accepts a
    # match against any org's token, not one global one.
    meta_verify_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
