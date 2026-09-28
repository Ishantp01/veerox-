"""Client-deployment-side application of a pending provisioning payload
(spec §4: idempotent provisioning with a unique central organization
reference, transactional local updates, preventing stale updates from
overwriting newer configuration).

Called in-process by workers/central_sync_worker.py after it pulls a
pending change from `GET /platform/sync/pending` — there is no longer an
HTTP endpoint a network caller can POST this to (the earlier signed-
assertion design had one, authenticated by a shared secret; removing
signing keys removed that authentication story too, so this is now applied
locally by the same process that already authenticated the PULL, rather
than exposing a second, separately-authenticated write surface).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.models.org import Org


async def apply_provisioning_locally(db: AsyncSession, payload: dict) -> tuple[Org, bool]:
    """Matches on `central_org_ref` (never a locally-generated primary key)
    and rejects (returns `applied=False`, not an error — this is the
    expected outcome of a retried/out-of-order duplicate) any payload whose
    `config_version` is not strictly greater than what's already applied
    locally. Returns `(org, applied)`; the caller (the sync worker) only
    acks upstream when `applied` is True.

    Safe under concurrent first-start: if two processes of the same fresh
    client deployment race to create the local org for the first time, the
    loser's INSERT hits `orgs.central_org_ref`'s unique constraint — caught
    here and turned into an UPDATE against the winner's now-committed row,
    rather than an unhandled exception or (worse) a second `orgs` row for
    the same organization.
    """
    central_org_ref = UUID(payload["central_org_ref"])
    result = await db.execute(select(Org).where(Org.central_org_ref == central_org_ref))
    org = result.scalar_one_or_none()

    if org is not None and payload["config_version"] <= org.config_version:
        return org, False

    license_expires_at: datetime | None = (
        datetime.fromisoformat(payload["license_expires_at"]) if payload.get("license_expires_at") else None
    )

    if org is None:
        org = Org(
            central_org_ref=central_org_ref,
            name=payload["name"],
            enabled_features=payload.get("enabled_features"),
            license_status=payload["license_status"],
            license_expires_at=license_expires_at,
            config_version=payload["config_version"],
            deployment_status="provisioned",
        )
        db.add(org)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            existing = (
                await db.execute(select(Org).where(Org.central_org_ref == central_org_ref))
            ).scalar_one_or_none()
            if existing is None:
                raise  # some other integrity failure — surface it
            if payload["config_version"] <= existing.config_version:
                return existing, False
            return await _update_org(db, existing, payload, license_expires_at)
        await db.refresh(org)
        return org, True

    return await _update_org(db, org, payload, license_expires_at)


async def _update_org(db: AsyncSession, org: Org, payload: dict, license_expires_at: datetime | None) -> tuple[Org, bool]:
    org.name = payload["name"]
    org.enabled_features = payload.get("enabled_features")
    org.license_status = payload["license_status"]
    org.license_expires_at = license_expires_at
    org.config_version = payload["config_version"]
    org.deployment_status = "provisioned"
    await db.commit()
    await db.refresh(org)
    return org, True
