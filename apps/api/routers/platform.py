"""Central licence + organization/feature management (spec: multi-tenant
licensing via direct server-to-server HTTPS validation).

Only ever mounted when `settings.deployment_mode == "owner"` (see
main.py's create_app) — a client deployment must never expose these
platform-owner administration endpoints. The machine-to-machine endpoints
at the bottom (`/license`, `/sync/pending`, `/sync/ack`) are the exception:
those ARE meant to be called by a client deployment, authenticated with its
own bearer token (`Authorization: Bearer <token>`, core/deployment_auth.py)
rather than a human session. There is no signed assertion anywhere in this
module — `/license` returns plain structured JSON; trust comes from HTTPS +
the bearer token, not an embedded signature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.api.config import settings
from apps.api.core.deployment_auth import DeploymentAuthDep, generate_deployment_token
from apps.api.core.deployment_status import compute_connection_status
from apps.api.core.security import hash_token
from apps.api.db.models.client_deployment import ClientDeployment
from apps.api.db.models.license_audit_event import LicenseAuditEvent
from apps.api.db.models.org import Org
from apps.api.db.models.sync_event import SyncEvent
from apps.api.deps import (
    DbDep,
    _org_is_platform_admin_owned,
    resolve_request_account_user_id,
    verify_platform_admin,
)
from apps.api.rate_limit import limiter
from apps.api.schemas.platform import (
    AllocateFeaturesIn,
    CreateOrganizationIn,
    CreateOrganizationOut,
    DeploymentSyncStatusOut,
    LicenseInfoOut,
    LicenseValidationOut,
    OrganizationIdentityOut,
    OrganizationOut,
    PendingSyncOut,
    RegisterDeploymentIn,
    SetupInstructionsOut,
    SyncAckIn,
    SyncAckOut,
)

PlatformAdminDep = Annotated[None, Depends(verify_platform_admin)]
ActorAccountUserDep = Annotated[UUID, Depends(resolve_request_account_user_id)]

router = APIRouter(prefix="/platform", tags=["platform"])


async def _connection_status_for(db: DbDep, org: Org) -> str:
    deployment = await db.get(ClientDeployment, org.client_deployment_id) if org.client_deployment_id else None
    return compute_connection_status(deployment, org)


def _organization_out(org: Org, connection_status: str) -> OrganizationOut:
    return OrganizationOut(
        id=str(org.id),
        central_org_ref=str(org.central_org_ref),
        name=org.name,
        enabled_features=org.enabled_features,
        deployment_status=org.deployment_status,
        config_version=org.config_version,
        license_status=org.license_status,
        client_deployment_id=str(org.client_deployment_id) if org.client_deployment_id else None,
        connection_status=connection_status,
    )


def _build_setup_instructions(org: Org, token: str) -> SetupInstructionsOut:
    """The owner-authorized one-time setup screen's content (organization
    creation itself never creates AWS resources — this is purely the
    configuration a human then applies to the client's own already-existing
    or soon-to-be-provisioned infrastructure). Uses the exact shared `.env`
    variable names; `DATABASE_URL` is always a clearly marked placeholder —
    this never contains the owner's own database credentials or any other
    owner secret."""
    license_api_url = settings.public_base_url
    env_snippet = (
        "DEPLOYMENT_MODE=client\n"
        f"DEFAULT_ORG_ID={org.central_org_ref}\n"
        f"LICENSE_API_URL={license_api_url}\n"
        f"LICENSE_API_TOKEN={token}\n"
        "DATABASE_URL=<client-database-connection>"
    )
    return SetupInstructionsOut(
        central_org_ref=str(org.central_org_ref),
        license_api_url=license_api_url,
        deployment_token=token,
        env_snippet=env_snippet,
    )


async def _create_default_deployment(db: DbDep, org: Org) -> str:
    """Shared by create_organization (the normal, automatic path) and
    register_deployment (the "prepare setup" path for an org that predates
    this feature) — generates the token, stores only its hash, and records
    the initial org_provision SyncEvent, all staged in the current
    transaction (the caller commits)."""
    token = generate_deployment_token()
    deployment = ClientDeployment(
        org_id=org.id, name="default", token_hash=hash_token(token), status="pending", config_version=org.config_version
    )
    db.add(deployment)
    await db.flush()

    org.client_deployment_id = deployment.id
    org.config_version += 1
    db.add(
        SyncEvent(
            org_id=org.id,
            deployment_id=deployment.id,
            event_type="org_provision",
            payload=_provisioning_payload(org),
            config_version=org.config_version,
        )
    )
    return token


@router.post("/organizations", response_model=CreateOrganizationOut, status_code=201)
async def create_organization(
    payload: CreateOrganizationIn,
    db: DbDep,
    actor: ActorAccountUserDep,
    _admin: PlatformAdminDep,
    response: Response,
) -> CreateOrganizationOut:
    """One click creates the organization, its allocated features, an
    optional first licence, AND its default client deployment registration
    (token generated, hash stored) — there is no separate "register
    deployment" step for the normal path. Only ever writes database
    records; never provisions AWS infrastructure.

    Safe against duplicate submissions: pass the same `idempotency_key` on a
    retried/double-clicked submission and this returns the already-created
    organization (with `setup: null`, since the token was already shown
    once) instead of creating a second org + deployment + token.
    """
    response.headers["Cache-Control"] = "no-store"

    if payload.idempotency_key:
        existing = (
            await db.execute(select(Org).where(Org.setup_idempotency_key == payload.idempotency_key))
        ).scalar_one_or_none()
        if existing is not None:
            return CreateOrganizationOut(
                organization=_organization_out(existing, await _connection_status_for(db, existing)), setup=None
            )

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name cannot be empty")

    org = Org(
        name=name,
        enabled_features=payload.enabled_features,
        max_team_members=payload.max_team_members,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_mobile=payload.contact_mobile,
        setup_idempotency_key=payload.idempotency_key,
    )
    db.add(org)
    await db.flush()

    if payload.license_days is not None:
        if payload.license_days <= 0:
            raise HTTPException(status_code=400, detail="license_days must be positive")
        org.license_status = "active"
        org.license_expires_at = datetime.now(UTC) + timedelta(days=payload.license_days)
        org.license_issued_at = datetime.now(UTC)
        org.license_duration_days = payload.license_days
        db.add(
            LicenseAuditEvent(
                org_id=org.id,
                action="issued",
                actor_account_user_id=actor,
                previous_status=None,
                new_status="active",
                previous_expires_at=None,
                new_expires_at=org.license_expires_at,
            )
        )

    token = await _create_default_deployment(db, org)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Two concurrent submits of the same idempotency_key raced past the
        # SELECT above — the loser here just returns the winner's row rather
        # than erroring the owner's retried click.
        if payload.idempotency_key:
            existing = (
                await db.execute(select(Org).where(Org.setup_idempotency_key == payload.idempotency_key))
            ).scalar_one_or_none()
            if existing is not None:
                return CreateOrganizationOut(
                    organization=_organization_out(existing, await _connection_status_for(db, existing)), setup=None
                )
        raise

    await db.refresh(org)
    return CreateOrganizationOut(
        organization=_organization_out(org, "awaiting_client_setup"),
        setup=_build_setup_instructions(org, token),
    )


@router.get("/organizations", response_model=list[OrganizationOut])
async def list_organizations(db: DbDep, _admin: PlatformAdminDep) -> list[OrganizationOut]:
    """Excludes the platform's own operating org, same convention as
    billing.py's list_orgs. Never includes a deployment token — only
    `connection_status`, the simple owner-facing signal."""
    result = await db.execute(select(Org).order_by(Org.created_at))
    orgs = [org for org in result.scalars().all() if not await _org_is_platform_admin_owned(db, org.id)]
    return [_organization_out(org, await _connection_status_for(db, org)) for org in orgs]


@router.patch("/organizations/{org_id}/features", response_model=OrganizationOut)
async def allocate_features(
    org_id: UUID, payload: AllocateFeaturesIn, db: DbDep, _admin: PlatformAdminDep
) -> OrganizationOut:
    """Change an org's allocated features. Bumps `Org.config_version` and,
    if the org already has a registered deployment, records a durable
    `feature_sync` SyncEvent — a client picks this up itself on its next
    `GET /platform/sync/pending` poll (pull-based; the owner never initiates
    an outbound call to a client). No env change or redeployment needed on
    the client side for this."""
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    org.enabled_features = payload.enabled_features
    org.config_version += 1
    await _enqueue_sync_if_deployed(db, org, event_type="feature_sync")

    await db.commit()
    await db.refresh(org)
    return _organization_out(org, await _connection_status_for(db, org))


async def _enqueue_sync_if_deployed(db: DbDep, org: Org, *, event_type: str) -> None:
    """Shared by allocate_features and billing.py's license actions —
    inserted in the SAME transaction as the owner-side change it reflects
    (spec §4's "transactional local updates"), so a sync event can never
    exist for a change that didn't actually commit, or be missing for one
    that did. Purely a durable audit/status record now — delivery is the
    client's own pull via GET /platform/sync/pending, not a push from here."""
    if org.client_deployment_id is None:
        return
    deployment = await db.get(ClientDeployment, org.client_deployment_id)
    if deployment is None or deployment.status == "revoked":
        return
    db.add(
        SyncEvent(
            org_id=org.id,
            deployment_id=deployment.id,
            event_type=event_type,
            payload=_provisioning_payload(org),
            config_version=org.config_version,
        )
    )


def _provisioning_payload(org: Org) -> dict:
    return {
        "central_org_ref": str(org.central_org_ref),
        "name": org.name,
        "enabled_features": org.enabled_features,
        "license_status": org.license_status,
        "license_expires_at": org.license_expires_at.isoformat() if org.license_expires_at else None,
        "config_version": org.config_version,
    }


@router.post("/organizations/{org_id}/deployments", response_model=SetupInstructionsOut, status_code=201)
async def register_deployment(
    org_id: UUID, payload: RegisterDeploymentIn, db: DbDep, response: Response, _admin: PlatformAdminDep
) -> SetupInstructionsOut:
    """"Prepare setup" for an organization that predates automatic
    deployment registration (every org created via POST /organizations from
    now on already has one — this is only needed for a legacy org that
    doesn't). Changes no business data: it only creates the deployment/token
    the normal creation flow would have created automatically. Refused if
    the org already has a deployment — use the revoke + this-again sequence,
    or the regenerate-token action below, instead of silently orphaning the
    existing one.
    """
    response.headers["Cache-Control"] = "no-store"
    _ = payload  # kept for a future named-deployment use case; unused today

    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org.client_deployment_id is not None:
        raise HTTPException(status_code=409, detail="Organization already has a registered deployment")

    token = await _create_default_deployment(db, org)
    await db.commit()
    await db.refresh(org)
    return _build_setup_instructions(org, token)


@router.post("/organizations/{org_id}/deployments/regenerate-token", response_model=SetupInstructionsOut)
async def regenerate_deployment_token(org_id: UUID, db: DbDep, response: Response, _admin: PlatformAdminDep) -> SetupInstructionsOut:
    """Owner-only "generate replacement setup token" action — used ONLY when
    the original setup token was lost before the client ever applied it (or
    needs to be invalidated for security reasons). This is NOT part of
    ordinary edits, renewals, synchronization, or restarts — none of those
    ever touch the token. Replacing it immediately invalidates the previous
    credential (only one `token_hash` is ever stored); the client's `.env`
    must be updated with the new value and the application restarted."""
    response.headers["Cache-Control"] = "no-store"

    org = await db.get(Org, org_id)
    if org is None or org.client_deployment_id is None:
        raise HTTPException(status_code=404, detail="Organization has no registered deployment")
    deployment = await db.get(ClientDeployment, org.client_deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")

    token = generate_deployment_token()
    deployment.token_hash = hash_token(token)
    await db.commit()
    return _build_setup_instructions(org, token)


@router.post("/organizations/{org_id}/deployments/revoke", status_code=204)
async def revoke_deployment(org_id: UUID, db: DbDep, _admin: PlatformAdminDep) -> None:
    """Immediately and permanently invalidates this org's deployment
    credential (spec §3). `core/deployment_auth.py::require_deployment_token`
    rejects a revoked deployment's token on its very next call, regardless
    of whether the token itself still hashes correctly."""
    org = await db.get(Org, org_id)
    if org is None or org.client_deployment_id is None:
        raise HTTPException(status_code=404, detail="Organization has no registered deployment")
    deployment = await db.get(ClientDeployment, org.client_deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")

    deployment.status = "revoked"
    await db.commit()


async def _build_sync_status(org_id: UUID, db: DbDep) -> DeploymentSyncStatusOut:
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org.client_deployment_id is None:
        raise HTTPException(status_code=404, detail="Organization has no registered deployment")
    deployment = await db.get(ClientDeployment, org.client_deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")

    pending_count = (
        await db.execute(
            select(func.count())
            .select_from(SyncEvent)
            .where(SyncEvent.deployment_id == deployment.id, SyncEvent.status == "pending")
        )
    ).scalar_one()

    return DeploymentSyncStatusOut(
        id=str(deployment.id),
        org_id=str(org.id),
        name=deployment.name,
        status=deployment.status,
        config_version=deployment.config_version,
        last_seen_at=deployment.last_seen_at.isoformat() if deployment.last_seen_at else None,
        last_sync_at=deployment.last_sync_at.isoformat() if deployment.last_sync_at else None,
        last_sync_status=deployment.last_sync_status,
        last_sync_error=deployment.last_sync_error,
        pending_events=pending_count,
    )


@router.get("/organizations/{org_id}/sync-status", response_model=DeploymentSyncStatusOut)
async def sync_status(org_id: UUID, db: DbDep, _admin: PlatformAdminDep) -> DeploymentSyncStatusOut:
    return await _build_sync_status(org_id, db)


@router.post("/organizations/{org_id}/sync-status/retry", response_model=DeploymentSyncStatusOut)
async def retry_sync(org_id: UUID, db: DbDep, _admin: PlatformAdminDep) -> DeploymentSyncStatusOut:
    """Safe retry action for the owner panel's sync-status screen (spec §4):
    re-queues the org's CURRENT state as a fresh SyncEvent rather than
    replaying whatever old failed payload existed, so a retry after the org
    was edited again always ships the latest configuration. The client picks
    it up on its own next poll — this does not push anything anywhere."""
    org = await db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org.client_deployment_id is None:
        raise HTTPException(status_code=404, detail="Organization has no registered deployment")

    await _enqueue_sync_if_deployed(db, org, event_type="org_provision")
    deployment = await db.get(ClientDeployment, org.client_deployment_id)
    deployment.last_sync_status = "pending"
    await db.commit()
    return await _build_sync_status(org_id, db)


# --- Machine-to-machine endpoints, called BY a client deployment ----------
# Authenticated with that deployment's own bearer token (Authorization:
# Bearer <token>), never a human session. The org/deployment identity is
# always derived FROM that token (DeploymentAuthDep), never taken from
# anything the caller submits.


@router.get("/license", response_model=LicenseValidationOut)
@limiter.limit("30/minute")
async def validate_license(request: Request, db: DbDep, deployment: DeploymentAuthDep) -> LicenseValidationOut:
    """Direct server-to-server licence validation (replaces the earlier
    signed-assertion design). A client deployment calls this on every cache
    refresh (core/license_cache.py) — not on every user request — and
    caches the structured result locally for a fixed 5 minutes (or until the
    licence's own expiry, if sooner)."""
    org = await db.get(Org, deployment.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    deployment_row = await db.get(ClientDeployment, deployment.id)
    deployment_row.last_seen_at = datetime.now(UTC)
    if deployment_row.status == "pending":
        deployment_row.status = "active"
    await db.commit()

    return LicenseValidationOut(
        organization=OrganizationIdentityOut(central_org_ref=str(org.central_org_ref), name=org.name),
        deployment_id=str(deployment.id),
        license=LicenseInfoOut(
            status=org.license_status,
            valid_from=org.license_issued_at.isoformat() if org.license_issued_at else None,
            expires_at=org.license_expires_at.isoformat() if org.license_expires_at else None,
        ),
        enabled_features=org.enabled_features,
        config_version=org.config_version,
    )


@router.get("/sync/pending", response_model=PendingSyncOut | None)
async def pending_sync(db: DbDep, deployment: DeploymentAuthDep) -> PendingSyncOut | None:
    """Reconciliation pull (spec §4): a client deployment polls this
    periodically to catch a configuration change — returns the org's
    current authoritative configuration if this deployment's own
    recorded `config_version` is behind the org's, else None (nothing
    pending). This is the ONLY way configuration reaches a client — the
    owner never calls out to a client (see module docstring)."""
    deployment_row = await db.get(ClientDeployment, deployment.id)
    deployment_row.last_seen_at = datetime.now(UTC)
    await db.commit()

    org = await db.get(Org, deployment.org_id)
    if org is None or org.config_version <= deployment_row.config_version:
        return None
    return PendingSyncOut(**_provisioning_payload(org))


@router.post("/sync/ack", response_model=SyncAckOut)
async def acknowledge_sync(payload: SyncAckIn, db: DbDep, deployment: DeploymentAuthDep) -> SyncAckOut:
    """A client deployment confirms it has applied configuration up to
    `config_version` — the owner records this as the deployment's own
    provisioning-status source of truth for the owner panel's sync-status
    screen (spec §4's "record acknowledgement in the owner service")."""
    deployment_row = await db.get(ClientDeployment, deployment.id)
    if payload.config_version < deployment_row.config_version:
        return SyncAckOut(acked=False, reason="stale config_version")

    deployment_row.config_version = payload.config_version
    deployment_row.last_sync_at = datetime.now(UTC)
    deployment_row.last_sync_status = "synced"
    deployment_row.last_sync_error = None

    await db.execute(
        SyncEvent.__table__.update()
        .where(
            SyncEvent.deployment_id == deployment.id,
            SyncEvent.config_version <= payload.config_version,
            SyncEvent.status != "delivered",
        )
        .values(status="delivered")
    )

    org = await db.get(Org, deployment.org_id)
    if org is not None and org.deployment_status != "provisioned":
        org.deployment_status = "provisioned"

    await db.commit()
    return SyncAckOut(acked=True)
