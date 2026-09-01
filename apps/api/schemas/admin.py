from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OutboundWhatsappIn(BaseModel):
    phone: str = Field(..., description="Recipient phone number in E.164 format.")
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
    whatsapp_phone_number_id: str | None
    # An org can have BOTH a dedicated Plivo and a dedicated Twilio number at
    # once — each field is independent (see routers/admin.py::update_org_numbers).
    plivo_phone_number: str | None
    twilio_phone_number: str | None = None


class OrgNumbersIn(BaseModel):
    # Empty/omit clears the corresponding number, falling back to the
    # platform default org for messages/calls on it (see
    # channels/whatsapp/adapter.py::_resolve_org_id and
    # channels/voice/webhook.py::_resolve_org_by_number).
    whatsapp_phone_number_id: str | None = Field(
        None, description="This org's WhatsApp Business phone_number_id, from the Meta dashboard."
    )
    plivo_phone_number: str | None = Field(
        None, description="This org's dedicated Plivo calling number, e.g. +14155551234."
    )
    twilio_phone_number: str | None = Field(
        None, description="This org's dedicated Twilio calling number, e.g. +14155551234."
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


class StatsTimeseriesPoint(BaseModel):
    date: str  # YYYY-MM-DD, UTC
    calls: int
    whatsapp_messages: int
    leads: int


class StatsTimeseriesOut(BaseModel):
    points: list[StatsTimeseriesPoint]
