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
    # True when org_id is the platform operator's own seeded org — every
    # Veerox staff account invited via POST /team/members onto that org gets
    # this, distinct from is_superuser (a narrower, individually-granted
    # flag). Drives frontend visibility of platform-team-only pages like the
    # cross-org support ticket queue (see deps.py's verify_platform_team_member).
    is_platform_org: bool = False


class MeOut(BaseModel):
    org_id: UUID
    org_name: str
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False
    is_platform_org: bool = False


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
    # Independent fields — an org can be provisioned with a dedicated number
    # on both providers at once, or just one, or neither.
    plivo_phone_number: str | None = Field(
        None, description="Dedicated Plivo calling number for this org, e.g. +14155551234. Optional."
    )
    twilio_phone_number: str | None = Field(
        None, description="Dedicated Twilio calling number for this org, e.g. +14155551234. Optional."
    )
    whatsapp_phone_number_id: str | None = Field(
        None, description="Dedicated WhatsApp Business phone_number_id for this org, from the Meta dashboard. Optional — falls back to the platform default."
    )


class ForgotTokenIn(BaseModel):
    identifier: str  # email address or E.164 mobile number


class ForgotTokenOut(BaseModel):
    message: str


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
