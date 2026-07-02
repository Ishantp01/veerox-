from __future__ import annotations

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


class KillSwitchIn(BaseModel):
    enabled: bool = Field(..., description="True to engage the kill switch, False to release it.")


class KillSwitchOut(BaseModel):
    enabled: bool


class PromptsOut(BaseModel):
    base: str
    voice_append: str
    whatsapp_append: str


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
