"""core/deployment_auth.py — bearer-token authentication for the machine-to-
machine endpoints a client deployment calls. Covers missing/invalid/revoked/
cross-client tokens and that a token is never trusted to name its own org
(identity is always derived from the token itself)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.db.models.client_deployment import ClientDeployment

ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


async def _create_org_and_deployment(client: AsyncClient, name: str) -> tuple[str, str]:
    """Organization creation now auto-registers the default deployment and
    generates its token in the same call — no separate register step."""
    create_resp = await client.post("/platform/organizations", json={"name": name}, headers=ADMIN_HEADERS)
    body = create_resp.json()
    return body["organization"]["id"], body["setup"]["deployment_token"]


async def test_missing_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/platform/license")
    assert response.status_code == 401


async def test_malformed_authorization_header_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/platform/license", headers={"Authorization": "not-a-bearer-token"})
    assert response.status_code == 401


async def test_unknown_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/platform/license", headers={"Authorization": "Bearer nonexistent-token"})
    assert response.status_code == 401


async def test_valid_token_authenticates_and_scopes_to_its_own_org(client: AsyncClient) -> None:
    org_id, token = await _create_org_and_deployment(client, "Alpha")
    response = await client.get("/platform/license", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["organization"]["central_org_ref"]


async def test_cross_client_token_cannot_reach_another_orgs_data(client: AsyncClient) -> None:
    """One client's token must never resolve to another client's org —
    identity always comes FROM the token, never from anything the caller
    submits (there is no org id in the request at all)."""
    org_a_id, token_a = await _create_org_and_deployment(client, "Beta")
    org_b_id, token_b = await _create_org_and_deployment(client, "Gamma")

    resp_a = await client.get("/platform/license", headers={"Authorization": f"Bearer {token_a}"})
    resp_b = await client.get("/platform/license", headers={"Authorization": f"Bearer {token_b}"})

    assert resp_a.json()["organization"]["central_org_ref"] != resp_b.json()["organization"]["central_org_ref"]
    assert org_a_id != org_b_id


async def test_revoked_token_is_rejected_even_though_it_still_matches(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    org_id, token = await _create_org_and_deployment(client, "Delta")
    await client.post(f"/platform/organizations/{org_id}/deployments/revoke", headers=ADMIN_HEADERS)

    response = await client.get("/platform/license", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


async def test_reissued_token_invalidates_the_old_one_immediately(client: AsyncClient) -> None:
    org_id, old_token = await _create_org_and_deployment(client, "Epsilon")
    reissue_resp = await client.post(
        f"/platform/organizations/{org_id}/deployments/regenerate-token", headers=ADMIN_HEADERS
    )
    new_token = reissue_resp.json()["deployment_token"]
    assert new_token != old_token

    old_resp = await client.get("/platform/license", headers={"Authorization": f"Bearer {old_token}"})
    assert old_resp.status_code == 401

    new_resp = await client.get("/platform/license", headers={"Authorization": f"Bearer {new_token}"})
    assert new_resp.status_code == 200


async def test_token_is_never_returned_by_any_read_endpoint(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only the register/reissue responses ever carry the raw token — no
    listing or status endpoint should ever echo it back, and the database
    itself only ever holds the hash."""
    org_id, token = await _create_org_and_deployment(client, "Zeta")

    sync_status_resp = await client.get(f"/platform/organizations/{org_id}/sync-status", headers=ADMIN_HEADERS)
    assert token not in sync_status_resp.text

    orgs_list_resp = await client.get("/platform/organizations", headers=ADMIN_HEADERS)
    assert token not in orgs_list_resp.text

    deployment = (
        await db_session.execute(select(ClientDeployment).where(ClientDeployment.org_id == uuid.UUID(org_id)))
    ).scalar_one()
    assert deployment.token_hash != token
    assert token not in deployment.token_hash
