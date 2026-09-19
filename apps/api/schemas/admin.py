from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from apps.api.core.phone import validate_country_code
from apps.api.schemas.org_numbers import OrgPhoneNumberIn, OrgPhoneNumberOut
from apps.api.schemas.whatsapp_common import TemplateButtonSendParam  # re-exported for existing importers


class OutboundWhatsappIn(BaseModel):
    phone: str = Field(..., description="Recipient phone number in E.164 format.")
    phone_number_id: str | None = Field(
        None,
        description=(
            "Which of the org's dedicated WhatsApp numbers to send from "
            "(an org_phone_numbers id, provider='whatsapp'). Omit to use "
            "the org's default WhatsApp number."
        ),
    )
    text: str | None = Field(
        None,
        min_length=1,
        description=(
            "Free-form message body. Deliverable only INSIDE the 24-hour "
            "customer-service window. Provide this OR a template_name."
        ),
    )
    template_name: str | None = Field(
        None,
        description=(
            "Approved WhatsApp template name. Required to message a user "
            "OUTSIDE the 24-hour window (free-form text fails with error 131047)."
        ),
    )
    template_lang: str = Field(
        "en_US", description="Template language code, e.g. 'en_US'."
    )
    template_params: list[str] | None = Field(
        None,
        description="Ordered values for the template body {{1}}, {{2}} ... placeholders.",
    )
    template_header_params: list[str] | None = Field(
        None,
        description="Value(s) for the template header's {{1}} placeholder, if it has one.",
    )
    template_button_params: list[TemplateButtonSendParam] | None = Field(
        None,
        description=(
            "Dynamic values for URL/COPY_CODE buttons — one entry per button that "
            "needs one, matched by its 0-based position in the template."
        ),
    )

    @model_validator(mode="after")
    def _require_text_or_template(self) -> OutboundWhatsappIn:
        if not self.text and not self.template_name:
            raise ValueError("Provide either 'text' or 'template_name'.")
        return self


class OutboundCallIn(BaseModel):
    to_phone: str = Field(..., description="Destination phone number in E.164 format.")
    # Optional — omit to keep the automatic Plivo-first/Twilio-fallback
    # ordering (channels/voice/failover.py). Only meaningful when the org
    # has BOTH a dedicated Plivo and Twilio number (the dashboard's calling
    # page only shows this choice in that case); the chosen provider is
    # just tried first, the other still stands by as a fallback.
    provider: Literal["plivo", "twilio"] | None = Field(
        None, description="Which dedicated number to call from, when the org has both."
    )


class KillSwitchIn(BaseModel):
    enabled: bool = Field(..., description="True to engage the kill switch, False to release it.")


class KillSwitchOut(BaseModel):
    enabled: bool


class PromptsOut(BaseModel):
    base: str
    voice_append: str
    whatsapp_append: str


class ScriptOut(BaseModel):
    script: str
    is_default: bool


class ScriptIn(BaseModel):
    # Empty/whitespace-only clears the org's override and reverts to the
    # platform default script.
    script: str | None = Field(None, description="Org's custom script. Empty/omit to reset to the default.")


class OrgNumbersOut(BaseModel):
    # An org can have several dedicated numbers per provider, including
    # several WhatsApp numbers (provider="whatsapp") — see
    # db/models/org_phone_number.py.
    phone_numbers: list[OrgPhoneNumberOut] = []


class OrgNumbersIn(BaseModel):
    # Omitted = the org's numbers are left untouched; present (including
    # []) = its full number set (Plivo, Twilio, and WhatsApp alike) is
    # replaced with this one (see
    # channels/voice/org_numbers.py::replace_org_phone_numbers). Clearing
    # all "whatsapp" entries falls back to the platform default org for
    # messages on any number no longer listed (see
    # channels/whatsapp/adapter.py::_resolve_org_id).
    phone_numbers: list[OrgPhoneNumberIn] | None = Field(
        None, description="This org's full set of dedicated Plivo/Twilio/WhatsApp numbers."
    )


class OutboundWhatsappOut(BaseModel):
    status: str
    phone: str
    text: str | None = None
    # Meta Graph API message id returned by send_text / send_template. None when
    # the local-dev fallback path was taken (META_ACCESS_TOKEN unset) — see
    # admin.outbound_whatsapp.
    wa_message_id: str | None = None


class OutboundCallOut(BaseModel):
    call_sid: str
    status: str


class WhatsAppSettingsOut(BaseModel):
    """Read-only status of the WhatsApp/Meta channel config. Secrets are
    reported as booleans only — the values themselves live in Render env
    vars (apps/api/config.py), not the DB, so there is nothing to edit here.
    """

    configured: bool = Field(
        ...,
        description="True when access token + phone number id are both set (real sends enabled).",
    )
    app_id_configured: bool
    app_secret_configured: bool
    verify_token_configured: bool
    access_token_configured: bool
    phone_number_id: str | None
    whatsapp_business_account_id: str | None
    graph_api_version: str
    webhook_url: str
    # Org's chosen template for the human-handoff notification
    # (core/tools.py::transfer_to_human). None = built-in default. Editable
    # via PUT /admin/settings/whatsapp.
    agent_connect_template_name: str | None = None


class WhatsAppSettingsIn(BaseModel):
    agent_connect_template_name: str | None = Field(
        None,
        description=(
            "Approved WhatsApp template name to use for the human-handoff "
            "notification. Must have exactly two body variables: {{1}} the "
            "caller's number, {{2}} the escalation reason. Omit/null to use "
            "the built-in default."
        ),
    )


class CallingSettingsOut(BaseModel):
    """Status of the voice calling config, plus the one editable setting on
    it — see WhatsAppSettingsOut for why the credential-status fields below
    stay view-only."""

    configured: bool = Field(
        ..., description="True when all Plivo credentials are set (real calls enabled)."
    )
    auth_id_configured: bool
    auth_token_configured: bool
    phone_number: str | None
    answer_webhook_url: str
    # Explicit override of failover.py's automatic Plivo-first/Twilio-
    # fallback ordering. None = automatic (the existing default: prefer
    # whichever provider this org has a dedicated number on).
    preferred_provider: Literal["plivo", "twilio"] | None = None


class CallingSettingsIn(BaseModel):
    preferred_provider: Literal["plivo", "twilio"] | None = Field(
        None,
        description=(
            "Which provider to try first for every outbound call this org places "
            "(single admin call, AI callback, campaign dialer, follow-up dispatcher). "
            "Omit/null to go back to automatic ordering."
        ),
    )


class CountryCodeSettingsOut(BaseModel):
    default_country_code: str


class CountryCodeSettingsIn(BaseModel):
    default_country_code: str

    @field_validator("default_country_code")
    @classmethod
    def _validate(cls, value: str) -> str:
        return validate_country_code(value)


class OpenAIKeySettingsOut(BaseModel):
    """Status of this org's own OpenAI key — see WhatsAppSettingsOut for why
    the real value never comes back over the API, only a masked preview."""

    configured: bool = Field(
        ..., description="True when this org has its own key set; false means it's on the platform key."
    )
    key_preview: str | None = Field(
        None, description="e.g. 'sk-...ab12' — last 4 characters only, never the full key."
    )


class OpenAIKeySettingsIn(BaseModel):
    api_key: str = Field(..., min_length=20, description="The org's OpenAI API key (starts with 'sk-').")


class PlivoCredentialsSettingsOut(BaseModel):
    """Status of this org's own Plivo account — no platform-wide fallback;
    an org with nothing configured here simply can't call/SMS via Plivo."""

    configured: bool
    auth_id: str | None = Field(None, description="Plivo Account SID — not secret, shown in full.")
    auth_token_preview: str | None = Field(None, description="e.g. 'sk-...ab12' — last 4 chars only.")


class PlivoCredentialsSettingsIn(BaseModel):
    auth_id: str = Field(..., min_length=1, description="Plivo Account Auth ID.")
    auth_token: str = Field(..., min_length=4, description="Plivo Account Auth Token.")


class TwilioCredentialsSettingsOut(BaseModel):
    """Status of this org's own Twilio account — no platform-wide fallback."""

    configured: bool
    account_sid: str | None = Field(None, description="Twilio Account SID — not secret, shown in full.")
    auth_token_preview: str | None = Field(None, description="e.g. 'sk-...ab12' — last 4 chars only.")


class TwilioCredentialsSettingsIn(BaseModel):
    account_sid: str = Field(..., min_length=1, description="Twilio Account SID.")
    auth_token: str = Field(..., min_length=4, description="Twilio Auth Token.")


class MetaCredentialsSettingsOut(BaseModel):
    """Status of this org's own Meta WhatsApp App — no platform-wide
    fallback; an org with nothing configured here simply can't send/receive
    WhatsApp through Meta."""

    configured: bool
    app_id: str | None = None
    app_secret_configured: bool = False
    app_secret_preview: str | None = None
    access_token_configured: bool = False
    access_token_preview: str | None = None
    business_account_id: str | None = None
    verify_token_configured: bool = False
    verify_token_preview: str | None = None


class MetaCredentialsSettingsIn(BaseModel):
    app_id: str = Field(..., min_length=1, description="Meta App ID.")
    app_secret: str = Field(..., min_length=4, description="Meta App Secret.")
    access_token: str = Field(..., min_length=4, description="Meta WhatsApp permanent access token.")
    business_account_id: str | None = Field(None, description="WhatsApp Business Account (WABA) id.")
    verify_token: str | None = Field(
        None, description="Token Meta's webhook handshake must present (hub.verify_token)."
    )


class StatsTimeseriesPoint(BaseModel):
    date: str  # YYYY-MM-DD, UTC
    calls: int
    whatsapp_messages: int
    leads: int


class StatsTimeseriesOut(BaseModel):
    points: list[StatsTimeseriesPoint]
