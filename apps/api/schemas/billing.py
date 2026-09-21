from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator

from apps.api.core.phone import validate_country_code
from apps.api.db.models.org import validate_org_features
from apps.api.schemas.org_numbers import OrgPhoneNumberIn, OrgPhoneNumberOut


class RegenerateAdminTokenOut(BaseModel):
    account_user_id: str
    email: str
    # Shown exactly once — only the SHA-256 digest is stored server-side.
    # The previous token stops working immediately.
    login_token: str


class OrgAdminOut(BaseModel):
    id: str
    name: str
    default_country_code: str = "+91"
    license_status: str
    license_expires_at: str | None = None
    license_issued_at: str | None = None
    license_duration_days: int | None = None
    license_notes: str | None = None
    seat_count: int
    admin_email: str | None
    admin_name: str | None = None
    admin_mobile: str | None = None
    created_at: str
    # Includes plivo/twilio/whatsapp entries alike — see
    # db/models/org_phone_number.py.
    phone_numbers: list[OrgPhoneNumberOut] = []
    # NULL = unrestricted (every AVAILABLE_ORG_FEATURES key allowed); an
    # explicit list (possibly empty) is what the platform admin set via the
    # Organizations page checklist — see deps.py's require_feature.
    enabled_features: list[str] | None = None
    # NULL = unlimited — see routers/team.py's invite_member.
    max_team_members: int | None = None


class OrgUpdateIn(BaseModel):
    """All fields optional — only what's sent gets changed (PATCH semantics).
    Deliberately excludes license fields: those are driven by the dedicated
    POST /billing/orgs/{id}/license/* actions instead of a direct field edit,
    so every change goes through one auditable path.

    `phone_numbers` omitted = the org's numbers (Plivo, Twilio, and WhatsApp
    alike) are left untouched; present (including `[]`) = its full number
    set is replaced with this one (see
    channels/voice/org_numbers.py::replace_org_phone_numbers).

    `admin_email`/`admin_name`/`admin_mobile` edit the org's own admin
    AccountUser (the earliest `role="admin"` membership) — same fields
    collected on creation (see ProvisionOrgIn), now editable afterward too.
    Deliberately excludes the login token itself: that's rotated via the
    dedicated POST /billing/orgs/{id}/regenerate-admin-token instead of a
    plain field edit, since a new token can only ever be shown once.

    Credential fields mirror ProvisionOrgIn's — same "both halves of a pair
    or neither" rule (e.g. plivo_auth_id needs plivo_auth_token alongside
    it to take effect), and omitting a pair leaves that provider's stored
    credentials untouched (there's no way to see/return them once
    encrypted, so the form always starts blank for these)."""

    name: str | None = None
    default_country_code: str | None = None
    admin_email: EmailStr | None = None
    admin_name: str | None = None
    admin_mobile: str | None = None
    phone_numbers: list[OrgPhoneNumberIn] | None = None
    plivo_auth_id: str | None = None
    plivo_auth_token: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_access_token: str | None = None
    meta_whatsapp_business_account_id: str | None = None
    meta_verify_token: str | None = None
    # Omitted = leave the org's current restriction untouched; an explicit
    # list (including []) replaces it; explicit null clears any restriction
    # back to "unrestricted" — see OrgAdminOut.enabled_features.
    enabled_features: list[str] | None = None
    max_team_members: int | None = None

    _validate_enabled_features = field_validator("enabled_features")(validate_org_features)

    @field_validator("default_country_code")
    @classmethod
    def _validate_country_code(cls, value: str | None) -> str | None:
        return None if value is None else validate_country_code(value)


class IssueLicenseIn(BaseModel):
    days: int
    notes: str | None = None


class RenewLicenseIn(BaseModel):
    # Omitted = reuse the org's last issued/renewed duration
    # (Org.license_duration_days), falling back to 30 days if it's never
    # been set — what a one-click "Renew" row action sends (see
    # QuickRenewButton in the frontend). A form-driven renewal in
    # ManageLicenseDialog always sends this explicitly.
    days: int | None = None


class ExtendLicenseIn(BaseModel):
    days: int


class SuspendLicenseIn(BaseModel):
    notes: str | None = None


class ReactivateLicenseIn(BaseModel):
    # Required only when the current expiry has already passed — otherwise
    # reactivating would immediately be flipped back to "expired" by the
    # background worker. Omitted = reuse the org's last issued/renewed
    # duration, same fallback as RenewLicenseIn.
    days: int | None = None


class PlatformSettingsOut(BaseModel):
    social_links: dict[str, str]


class PlatformSettingsUpdateIn(BaseModel):
    """All fields optional — only what's sent gets changed (PATCH semantics)."""

    social_links: dict[str, str] | None = None


class SocialLinksOut(BaseModel):
    social_links: dict[str, str]
