"""Deployment credentials for server-to-server licence validation.

A client deployment authenticates every call to the owner API with a single
bearer token, sent as `Authorization: Bearer <token>` over HTTPS — never a
query param, never in a body, never logged. The token is generated with a
cryptographically secure RNG (`secrets.token_urlsafe`, >=256 bits), bound to
one org's one `ClientDeployment` row, shown to the platform admin exactly
once at generation, and stored server-side only as a SHA-256 hash
(`core/security.py::hash_token` — the same one-way convention already used
for `AccountUser.token_hash`). The owner never needs to recover the raw
token: it looks up the deployment BY the hash of whatever token was
presented, an indexed equality lookup — there is no separate "key" or
client-submitted identifier to trust (spec: "never trust a client-submitted
organization ID alone" / "the central API must derive the authorized
client/deployment from the token").

A revoked deployment's token is rejected immediately regardless of whether
it still hashes correctly — `ClientDeployment.status == "revoked"` is
checked before anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select

from apps.api.core.security import generate_login_token, hash_token
from apps.api.db.models.client_deployment import ClientDeployment
from apps.api.deps import DbDep

# secrets.token_urlsafe(32) draws 32 raw random bytes (256 bits) before
# base64url-encoding them — reusing core/security.py's existing
# generate_login_token (already exactly this) rather than duplicating it,
# since the strength requirement (CSPRNG, >=256 bits) is identical.
generate_deployment_token = generate_login_token


@dataclass(frozen=True)
class AuthenticatedDeployment:
    id: UUID
    org_id: UUID
    config_version: int


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


async def require_deployment_token(
    db: DbDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedDeployment:
    """FastAPI dependency for every owner endpoint a client deployment calls
    (`GET /platform/license`, `GET /platform/sync/pending`, `POST
    /platform/sync/ack`). Rejects a missing/malformed header, an unknown
    token, and a revoked deployment — in all three cases with the same
    401/403, never distinguishing "wrong token" from "unknown token" in a
    way that would help a caller enumerate valid tokens.
    """
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    result = await db.execute(
        select(ClientDeployment).where(ClientDeployment.token_hash == hash_token(token))
    )
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status_code=401, detail="Invalid deployment token")
    if deployment.status == "revoked":
        raise HTTPException(status_code=403, detail="Deployment token revoked")

    return AuthenticatedDeployment(
        id=deployment.id, org_id=deployment.org_id, config_version=deployment.config_version
    )


DeploymentAuthDep = Annotated[AuthenticatedDeployment, Depends(require_deployment_token)]
