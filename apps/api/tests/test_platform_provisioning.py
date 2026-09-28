"""routers/platform.py + core/provisioning_apply.py — one-click organization
creation (auto-registering its default deployment + token), duplicate-
submission safety, connection status, licence audit trail, sync-status, and
idempotent/out-of-order/concurrent provisioning application."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.provisioning_apply import apply_provisioning_locally
from apps.api.db.models.client_deployment import ClientDeployment
from apps.api.db.models.license_audit_event import LicenseAuditEvent
from apps.api.db.models.org import Org
from apps.api.db.models.sync_event import SyncEvent

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def test_create_organization_auto_registers_deployment_and_token(client: AsyncClient) -> None:
    """One click: creating the organization also creates its default
    deployment registration and generates its token in the SAME call — no
    separate "register deployment" step. Creating the org never implies AWS
    infrastructure exists (deployment_status starts "pending_deployment")."""
    response = await client.post(
        "/platform/organizations",
        json={"name": "Acme Client", "enabled_features": ["crm", "leads"]},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()

    org = body["organization"]
    assert org["deployment_status"] == "pending_deployment"
    assert org["enabled_features"] == ["crm", "leads"]
    assert org["client_deployment_id"] is not None
    assert org["connection_status"] == "awaiting_client_setup"

    setup = body["setup"]
    assert setup["deployment_token"]
    assert setup["central_org_ref"] == org["central_org_ref"]
    assert setup["license_api_url"]
    assert "DEPLOYMENT_MODE=client" in setup["env_snippet"]
    assert f"LICENSE_API_TOKEN={setup['deployment_token']}" in setup["env_snippet"]
    assert "DATABASE_URL=<client-database-connection>" in setup["env_snippet"]

    pending_events = (
        await client.get(f"/platform/organizations/{org['id']}/sync-status", headers=ADMIN_HEADERS)
    ).json()
    assert pending_events["pending_events"] == 1


async def test_duplicate_submission_with_same_idempotency_key_does_not_duplicate(client: AsyncClient) -> None:
    """A double-click/retried submission of the SAME form (same
    idempotency_key) must return the already-created organization, not
    create a second org + deployment + token."""
    payload = {"name": "Beta Client", "idempotency_key": "beta-client-form-submit-1"}

    first = await client.post("/platform/organizations", json=payload, headers=ADMIN_HEADERS)
    second = await client.post("/platform/organizations", json=payload, headers=ADMIN_HEADERS)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["organization"]["id"] == second.json()["organization"]["id"]
    # The token is shown exactly once — a duplicate resubmit does not
    # regenerate or re-reveal it.
    assert second.json()["setup"] is None
    assert first.json()["setup"]["deployment_token"]

    orgs = (await client.get("/platform/organizations", headers=ADMIN_HEADERS)).json()
    matching = [o for o in orgs if o["name"] == "Beta Client"]
    assert len(matching) == 1


# A truly-concurrent (two requests racing past the dedupe SELECT before
# either commits) HTTP-level version of the test above isn't meaningful
# under this suite's shared `client` fixture: it wires every request in a
# test to the SAME single `db_session`/connection, so two requests "at once"
# via asyncio.gather don't get the independent connections/transactions two
# real concurrent processes would — sharing one AsyncSession across
# concurrent coroutines is unsupported by SQLAlchemy regardless of the code
# under test. The actual race-recovery code path (an IntegrityError on the
# losing INSERT, recovered by re-reading and updating the winner's row) is
# exercised with genuinely separate connections in
# test_concurrent_first_start_does_not_create_duplicate_local_org below,
# which uses a real on-disk SQLite file for that reason.


async def test_license_actions_are_recorded_in_audit_history(client: AsyncClient, db_session: AsyncSession) -> None:
    create_resp = await client.post("/platform/organizations", json={"name": "Gamma Client"}, headers=ADMIN_HEADERS)
    org_id = create_resp.json()["organization"]["id"]

    await client.post(f"/billing/orgs/{org_id}/license/issue", json={"days": 30}, headers=ADMIN_HEADERS)
    await client.post(f"/billing/orgs/{org_id}/license/suspend", json={"notes": "dispute"}, headers=ADMIN_HEADERS)
    await client.post(f"/billing/orgs/{org_id}/license/revoke", json={"notes": "ended"}, headers=ADMIN_HEADERS)
    await client.post(f"/billing/orgs/{org_id}/license/reactivate", json={"days": 30}, headers=ADMIN_HEADERS)

    events = (
        await db_session.execute(
            select(LicenseAuditEvent)
            .where(LicenseAuditEvent.org_id == uuid.UUID(org_id))
            .order_by(LicenseAuditEvent.created_at)
        )
    ).scalars().all()
    assert [e.action for e in events] == ["issued", "suspended", "revoked", "reactivated"]


async def test_connection_status_progresses_through_setup(client: AsyncClient, db_session: AsyncSession) -> None:
    """Never "connected" merely because a token was generated — only once
    the client has authenticated (GET /platform/license) AND acknowledged
    the current configuration (POST /platform/sync/ack)."""
    create_resp = await client.post("/platform/organizations", json={"name": "Theta Client"}, headers=ADMIN_HEADERS)
    body = create_resp.json()
    org_id, token = body["organization"]["id"], body["setup"]["deployment_token"]
    assert body["organization"]["connection_status"] == "awaiting_client_setup"

    # Authenticate but don't yet ack — sync_pending, not connected.
    await client.get("/platform/license", headers={"Authorization": f"Bearer {token}"})
    orgs_after_auth = (await client.get("/platform/organizations", headers=ADMIN_HEADERS)).json()
    theta = next(o for o in orgs_after_auth if o["id"] == org_id)
    assert theta["connection_status"] == "sync_pending"

    # Pull + ack the pending config — now connected.
    pending = (
        await client.get("/platform/sync/pending", headers={"Authorization": f"Bearer {token}"})
    ).json()
    await client.post(
        "/platform/sync/ack",
        json={"config_version": pending["config_version"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    orgs_after_ack = (await client.get("/platform/organizations", headers=ADMIN_HEADERS)).json()
    theta = next(o for o in orgs_after_ack if o["id"] == org_id)
    assert theta["connection_status"] == "connected"


async def test_revoked_deployment_shows_connection_issue(client: AsyncClient) -> None:
    create_resp = await client.post("/platform/organizations", json={"name": "Iota Client"}, headers=ADMIN_HEADERS)
    org_id = create_resp.json()["organization"]["id"]
    await client.post(f"/platform/organizations/{org_id}/deployments/revoke", headers=ADMIN_HEADERS)

    orgs = (await client.get("/platform/organizations", headers=ADMIN_HEADERS)).json()
    iota = next(o for o in orgs if o["id"] == org_id)
    assert iota["connection_status"] == "connection_issue"


async def test_prepare_setup_for_organization_without_a_deployment(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An organization created before this feature (or any other way that
    left it without a deployment) can have one prepared without touching its
    business data — the safe path for existing organizations."""
    org = Org(name="Legacy Co", enabled_features=["crm"])
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    response = await client.post(
        f"/platform/organizations/{org.id}/deployments", json={}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["deployment_token"]
    assert body["central_org_ref"] == str(org.central_org_ref)

    await db_session.refresh(org)
    assert org.name == "Legacy Co"  # untouched
    assert org.enabled_features == ["crm"]  # untouched
    assert org.client_deployment_id is not None

    # Preparing setup again is refused, not silently orphaning the first.
    second = await client.post(f"/platform/organizations/{org.id}/deployments", json={}, headers=ADMIN_HEADERS)
    assert second.status_code == 409


async def test_pending_sync_reflects_current_org_state(client: AsyncClient) -> None:
    create_resp = await client.post("/platform/organizations", json={"name": "Eta Client"}, headers=ADMIN_HEADERS)
    body = create_resp.json()
    org_id, token = body["organization"]["id"], body["setup"]["deployment_token"]

    pending_resp = await client.get("/platform/sync/pending", headers={"Authorization": f"Bearer {token}"})
    assert pending_resp.status_code == 200
    pending_body = pending_resp.json()
    assert pending_body is not None
    assert pending_body["name"] == "Eta Client"

    ack_resp = await client.post(
        "/platform/sync/ack",
        json={"config_version": pending_body["config_version"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ack_resp.json()["acked"] is True

    # Nothing pending now that this deployment is caught up.
    second_pending = await client.get("/platform/sync/pending", headers={"Authorization": f"Bearer {token}"})
    assert second_pending.json() is None

    sync_status_resp = await client.get(f"/platform/organizations/{org_id}/sync-status", headers=ADMIN_HEADERS)
    assert sync_status_resp.json()["last_sync_status"] == "synced"


async def test_feature_update_requires_no_new_registration(client: AsyncClient) -> None:
    """Renewal/feature changes synchronize automatically — no new
    deployment/token/env change is ever needed for them."""
    create_resp = await client.post("/platform/organizations", json={"name": "Kappa Client"}, headers=ADMIN_HEADERS)
    body = create_resp.json()
    org_id, token = body["organization"]["id"], body["setup"]["deployment_token"]

    await client.patch(
        f"/platform/organizations/{org_id}/features", json={"enabled_features": ["crm"]}, headers=ADMIN_HEADERS
    )

    pending = (await client.get("/platform/sync/pending", headers={"Authorization": f"Bearer {token}"})).json()
    assert pending is not None
    assert pending["enabled_features"] == ["crm"]


async def test_out_of_order_and_duplicate_apply_are_idempotent(db_session: AsyncSession) -> None:
    """First-time provisioning + duplicate retries + out-of-order
    synchronization, exercised directly against
    core/provisioning_apply.py::apply_provisioning_locally — the function a
    client's central_sync_worker calls in-process after an authenticated
    pull."""
    central_ref = str(uuid.uuid4())
    payload_v1 = {
        "central_org_ref": central_ref,
        "name": "Delta Client",
        "enabled_features": ["crm"],
        "license_status": "active",
        "license_expires_at": None,
        "config_version": 1,
    }

    org, applied = await apply_provisioning_locally(db_session, payload_v1)
    assert applied is True

    # Duplicate retry of the SAME version must not error and must not
    # double-apply.
    org, applied = await apply_provisioning_locally(db_session, payload_v1)
    assert applied is False

    # A newer version applies cleanly.
    payload_v2 = {**payload_v1, "enabled_features": ["crm", "leads"], "config_version": 2}
    org, applied = await apply_provisioning_locally(db_session, payload_v2)
    assert applied is True

    # An OLDER/stale version arriving after the newer one must be rejected,
    # never regressing local state.
    org, applied = await apply_provisioning_locally(db_session, payload_v1)
    assert applied is False

    result = await db_session.execute(select(Org).where(Org.central_org_ref == uuid.UUID(central_ref)))
    org = result.scalar_one()
    assert org.enabled_features == ["crm", "leads"]
    assert org.config_version == 2


async def test_concurrent_first_start_does_not_create_duplicate_local_org(tmp_path) -> None:
    """Two processes of the same fresh client deployment racing to apply
    their first-ever provisioning payload must not end up with two `orgs`
    rows for the same organization — one wins the INSERT, the other
    recovers via the unique constraint on `central_org_ref` and updates the
    winner's row instead.

    Needs genuinely separate DB connections/transactions per "process" to
    mean anything — the shared in-memory SQLite `test_engine` fixture used
    elsewhere in this suite runs every session through one single pooled
    connection (StaticPool, required so schema/data are visible across
    sessions at all for `:memory:`), which makes two "concurrent" sessions
    actually share one transaction context — great for ordinary tests, but
    it makes one session's rollback silently wipe out the other's uncommitted
    work, which isn't how two real separate Postgres connections behave in
    production. A real on-disk SQLite file gives each session its own
    connection/transaction, matching that production isolation closely
    enough to exercise the actual race.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.api.db.base import Base

    db_path = tmp_path / "concurrent_first_start.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    central_ref = str(uuid.uuid4())
    payload = {
        "central_org_ref": central_ref,
        "name": "Concurrent Startup Co",
        "enabled_features": None,
        "license_status": "active",
        "license_expires_at": None,
        "config_version": 1,
    }

    async def _apply() -> bool:
        async with session_factory() as session:
            _, applied = await apply_provisioning_locally(session, payload)
            return applied

    try:
        results = await asyncio.gather(_apply(), _apply())
        assert results.count(True) == 1  # exactly one insert actually happened

        async with session_factory() as session:
            rows = (
                await session.execute(select(Org).where(Org.central_org_ref == uuid.UUID(central_ref)))
            ).scalars().all()
            assert len(rows) == 1
    finally:
        await engine.dispose()


async def test_direct_api_call_without_credentials_is_rejected(client: AsyncClient) -> None:
    """Direct API bypass attempt: hitting a protected owner endpoint with no
    credential at all must fail, not silently default to some org."""
    response = await client.get("/platform/organizations")
    assert response.status_code == 403

    response = await client.post("/platform/organizations", json={"name": "Should Not Work"})
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/billing/orgs", "/platform/organizations"])
async def test_platform_admin_endpoints_reject_ordinary_session(
    client: AsyncClient, db_session: AsyncSession, path: str
) -> None:
    """An ordinary (non-superuser) org admin session must not reach
    platform-owner endpoints, even with a valid session — this is the
    client-admin-cannot-obtain-platform-owner-access guarantee."""
    from apps.api.core.security import generate_login_token, hash_token
    from apps.api.db.models.account_user import AccountUser
    from apps.api.db.models.org import Org as OrgModel
    from apps.api.db.models.org_membership import OrgMembership

    org = OrgModel(name="Ordinary Co")
    db_session.add(org)
    await db_session.flush()
    token = generate_login_token()
    account = AccountUser(email="admin@ordinaryco.example", token_hash=hash_token(token))
    db_session.add(account)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=account.id, role="admin"))
    await db_session.commit()

    login_resp = await client.post("/auth/login", json={"token": token})
    session_token = login_resp.json()["token"]

    response = await client.get(path, headers={"X-Session-Token": session_token})
    assert response.status_code == 403


async def test_platform_owner_exemption_bypasses_license_checks(db_session: AsyncSession) -> None:
    """An org with a superuser member is exempt from licence checks
    entirely (the trusted Veerox platform-owner exemption) — even with no
    licence issued at all, unlike an ordinary client org (covered by
    test_platform_admin_endpoints_reject_ordinary_session above, which
    confirms an ordinary org admin cannot reach platform-owner endpoints in
    the first place)."""
    from apps.api.core.security import generate_login_token, hash_token
    from apps.api.db.models.account_user import AccountUser
    from apps.api.db.models.org_membership import OrgMembership
    from apps.api.deps import is_org_license_active

    org = Org(name="Platform Ops")
    db_session.add(org)
    await db_session.flush()
    token = generate_login_token()
    owner = AccountUser(email="owner@veerox.example", token_hash=hash_token(token), is_superuser=True)
    db_session.add(owner)
    await db_session.flush()
    db_session.add(OrgMembership(org_id=org.id, account_user_id=owner.id, role="admin"))
    await db_session.commit()

    assert await is_org_license_active(db_session, org.id) is True  # no licence issued at all, still exempt
