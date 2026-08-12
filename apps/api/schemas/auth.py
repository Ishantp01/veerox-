from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginIn(BaseModel):
    token: str


class SessionOut(BaseModel):
    token: str
    org_id: UUID
    org_name: str
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False


class MeOut(BaseModel):
    org_id: UUID
    org_name: str
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False


class ProvisionOrgIn(BaseModel):
    org_name: str
    email: EmailStr
    full_name: str | None = None
    # E.164 mobile number the login token is SMS'd to (see
    # routers/auth.py's provision_org).
    mobile: str
    # Optional dedicated numbers for this org. Left unset (None), inbound
    # calls/messages on the platform default numbers keep resolving to this
    # org until an admin sets these later via PUT /admin/org-numbers (see
    # Org.plivo_phone_number / Org.twilio_phone_number / Org.whatsapp_phone_number_id).
    plivo_phone_number: str | None = Field(
        None,
        description=(
            "Dedicated calling number for this org, e.g. +14155551234 — either a Plivo "
            "or a Twilio number; the server checks both accounts and stores it under "
            "whichever one owns it (see channels/voice/number_provider.py). Optional — "
            "falls back to the platform default."
        ),
    )
    whatsapp_phone_number_id: str | None = Field(
        None, description="Dedicated WhatsApp Business phone_number_id for this org, from the Meta dashboard. Optional — falls back to the platform default."
    )


class ProvisionOrgOut(BaseModel):
    org_id: UUID
    account_user_id: UUID
    email: str
    # Shown exactly once — only the SHA-256 digest is stored server-side
    # (see core/security.py), so this is the only chance to hand it to
    # whoever is provisioning the account.
    login_token: str
    # True when the login token was also SMS'd to `mobile` successfully;
    # False means the SMS failed and the token above is the only copy.
    sms_sent: bool
