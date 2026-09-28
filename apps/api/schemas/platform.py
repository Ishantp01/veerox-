from __future__ import annotations

from pydantic import BaseModel, field_validator

from apps.api.db.models.org import validate_org_features


class CreateOrganizationIn(BaseModel):
    """Owner-panel "create organization" form. One click creates the org,
    its allocated features, an optional first licence, AND its default
    client deployment registration (token generation included) — there is
    no separate "register deployment" step for the normal path. This never
    implies any AWS/client infrastructure exists yet — only a database
    record (see Org.deployment_status, which starts "pending_deployment"
    until the client's own first successful check-in)."""

    name: str
    enabled_features: list[str] | None = None
    # Optional first licence, issued in the same call so a brand-new org can
    # be created "licensed" in one step — omit to leave it license-less
    # (routers/billing.py's issue_license can always be called later).
    license_days: int | None = None
    # Optional cap on invited team members — same field/semantics as
    # billing.py's OrgUpdateIn.max_team_members. None = unlimited.
    max_team_members: int | None = None
    # Plain contact-person info — this org has no real login account in the
    # owner's database (its own separate server has its own users), so this
    # is purely who the platform admin should reach out to about it. Same
    # fields/semantics as billing.py's OrgUpdateIn.contact_name/
    # contact_email/contact_mobile, editable later from the same place.
    contact_name: str | None = None
    contact_email: str | None = None
    contact_mobile: str | None = None
    # Optional caller-supplied dedupe key — resubmitting the same form
    # (double-click, retried request after a dropped response) with the
    # SAME key returns the already-created organization instead of creating
    # a duplicate org + a second deployment/token.
    idempotency_key: str | None = None

    _validate_enabled_features = field_validator("enabled_features")(validate_org_features)


class OrganizationOut(BaseModel):
    id: str
    central_org_ref: str
    name: str
    enabled_features: list[str] | None
    deployment_status: str
    config_version: int
    license_status: str
    client_deployment_id: str | None
    # One of CONNECTION_STATUSES (core/deployment_status.py) — the simple,
    # non-technical status the owner panel shows. Never "connected" merely
    # because a token was generated; that requires an actual authenticated,
    # acknowledged check-in.
    connection_status: str


class AllocateFeaturesIn(BaseModel):
    enabled_features: list[str] | None

    _validate_enabled_features = field_validator("enabled_features")(validate_org_features)


class RegisterDeploymentIn(BaseModel):
    name: str = "default"


class SetupInstructionsOut(BaseModel):
    """Shown exactly once, immediately after a deployment token is
    generated (either as part of organization creation, or as a later
    "prepare setup" / "generate replacement setup token" action) — never
    retrievable again afterward. Only this response, and no other endpoint,
    ever carries the raw `deployment_token`; the server stores only its
    hash. The caller must not cache this response (see the `Cache-Control:
    no-store` header set on every endpoint that returns one of these)."""

    central_org_ref: str
    license_api_url: str
    deployment_token: str
    # Copy-pasteable block using the exact shared `.env` variable names
    # (DEPLOYMENT_MODE, DEFAULT_ORG_ID, LICENSE_API_URL, LICENSE_API_TOKEN,
    # DATABASE_URL) — DATABASE_URL is always a clearly marked placeholder;
    # this snippet never contains owner database credentials or any other
    # owner secret.
    env_snippet: str


class CreateOrganizationOut(BaseModel):
    organization: OrganizationOut
    # None only when this call was an idempotent duplicate resubmit that
    # returned an already-existing organization — the token was already
    # shown once on the original call and is never regenerated just to
    # satisfy a retry.
    setup: SetupInstructionsOut | None = None


class DeploymentSyncStatusOut(BaseModel):
    id: str
    org_id: str
    name: str
    status: str
    config_version: int
    last_seen_at: str | None
    last_sync_at: str | None
    last_sync_status: str
    last_sync_error: str | None
    pending_events: int


class LicenseInfoOut(BaseModel):
    status: str
    valid_from: str | None
    expires_at: str | None


class OrganizationIdentityOut(BaseModel):
    central_org_ref: str
    name: str


class LicenseValidationOut(BaseModel):
    """GET /platform/license's response body — structured JSON, not a
    signed token. A client deployment trusts this because the connection is
    HTTPS + the request was authenticated with its own bearer token, not
    because of anything cryptographically embedded in the payload."""

    organization: OrganizationIdentityOut
    deployment_id: str
    license: LicenseInfoOut
    enabled_features: list[str] | None
    config_version: int


class PendingSyncOut(BaseModel):
    """GET /platform/sync/pending's response body when this deployment's
    `config_version` is behind the org's — the configuration it should
    apply locally. Mirrors LicenseValidationOut's shape (both describe "this
    org's current authoritative configuration") but is fetched via a
    separate, deliberately named endpoint so a client's licence-check cadence
    and its provisioning-sync cadence can be reasoned about (and rate
    limited) independently."""

    central_org_ref: str
    name: str
    enabled_features: list[str] | None
    license_status: str
    license_expires_at: str | None
    config_version: int


class SyncAckIn(BaseModel):
    config_version: int


class SyncAckOut(BaseModel):
    acked: bool
    reason: str | None = None
