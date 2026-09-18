from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from apps.api.db.models.org import validate_org_features
from apps.api.schemas.org_numbers import OrgPhoneNumberIn


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
    # Lets the dashboard gate immediately on load rather than waiting for an
    # incidental 403 from some other request — deps.py's enforce_org_license
    # is still the real enforcement, this is display-only. Always "active"/
    # null for is_platform_org (mirrors that dependency's own exemption).
    license_status: str = "active"
    license_expires_at: str | None = None
    # Lets the dashboard hide nav items/pages for a feature the org has been
    # restricted from, same display-only role as license_status above —
    # deps.py's require_feature is still the real enforcement. null =
    # unrestricted (every AVAILABLE_ORG_FEATURES key allowed). Always null
    # for is_platform_org.
    enabled_features: list[str] | None = None


class MeOut(BaseModel):
    org_id: UUID
    org_name: str
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False
    is_platform_org: bool = False
    license_status: str = "active"
    license_expires_at: str | None = None
    enabled_features: list[str] | None = None


class ProvisionOrgIn(BaseModel):
    org_name: str
    email: EmailStr
    full_name: str | None = None
    # E.164 mobile number the login token is SMS'd to (see
    # routers/auth.py's provision_org).
    mobile: str
    # Optional dedicated numbers for this org — any mix of Plivo/Twilio/
    # WhatsApp entries, several per provider allowed. Left empty, inbound
    # calls/messages on the platform default numbers keep resolving to this
    # org until an admin sets these later via PATCH /billing/orgs/{id} or
    # PUT /admin/org-numbers (see db/models/org_phone_number.py).
    phone_numbers: list[OrgPhoneNumberIn] = Field(default_factory=list)

    # Optional — this org's own Plivo/Twilio/Meta WhatsApp credentials, set
    # at creation time instead of (or in addition to, if changed later)
    # PUT /admin/settings/{plivo,twilio,meta}-credentials. There is no
    # platform-wide fallback: an org with none of these set simply can't
    # call/SMS/WhatsApp until an admin configures them, here or later.
    plivo_auth_id: str | None = None
    plivo_auth_token: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_access_token: str | None = None
    meta_whatsapp_business_account_id: str | None = None
    meta_verify_token: str | None = None

    # Optional — this org's own OpenAI key, set at creation time instead of
    # (or in addition to, if changed later) PUT /admin/settings/openai-key.
    # Left unset, the org bills against the platform's shared key (see
    # core/org_openai_key.py::resolve_openai_api_key).
    openai_api_key: str | None = None

    # Optional — restricts this org to a subset of AVAILABLE_ORG_FEATURES
    # from the start (unset = unrestricted) and/or caps its team size (unset
    # = unlimited). Both are editable later via PATCH /billing/orgs/{id} —
    # see schemas/billing.py's OrgUpdateIn.
    enabled_features: list[str] | None = None
    max_team_members: int | None = None

    _validate_enabled_features = field_validator("enabled_features")(validate_org_features)


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
