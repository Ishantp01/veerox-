from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db.base import Base

# "pending": registered on the owner side but has never successfully called
# in (no token exchange confirmed yet). "active": has successfully
# authenticated at least once. "revoked": platform admin has pulled its
# token — every owner endpoint gated by core/deployment_auth.py rejects it
# immediately regardless of the token presented (also the row-level effect
# of a token replacement — see routers/platform.py's
# regenerate_deployment_token, which overwrites token_hash rather than
# changing status, but the OLD token is functionally revoked the instant
# that commits since only one hash is ever stored).
CLIENT_DEPLOYMENT_STATUSES = ("pending", "active", "revoked")

# Mirrors ClientDeployment.last_sync_status below.
SYNC_STATUSES = ("pending", "synced", "failed")


class ClientDeployment(Base):
    """A registered client backend — one row per physically separate client
    deployment (its own app environment + database + secrets, see the
    architecture doc at docs/multi-tenant-licensing.md). Lives only in the
    owner database; a client deployment never has a row for itself, only for
    the org(s) it was provisioned for via Org.client_deployment_id.

    `token_hash` is the SHA-256 digest of this deployment's bearer token
    (`core/security.py::hash_token`, same one-way convention as
    `AccountUser.token_hash`) — the raw token is generated once
    (`core/deployment_auth.py::generate_deployment_token`, >=256 bits of
    CSPRNG randomness), shown to the platform admin exactly once, and never
    stored or recoverable server-side afterward. The client sends it back on
    every call as `Authorization: Bearer <token>`; the owner looks the
    deployment up BY that hash (an indexed equality lookup — the hash *is*
    the lookup key, there is no separate public identifier), so an org
    never has to be trusted from anything the caller submits (spec: "never
    trust a client-submitted organization ID alone").
    """

    __tablename__ = "client_deployments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    org_id: Mapped[UUID] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    # Bumped every time this org's licence/features change on the owner side
    # (routers/platform.py, billing.py's license endpoints) — carried in
    # every SyncEvent and returned by GET /platform/license, so a client
    # polling GET /platform/sync/pending can tell whether it's caught up.
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
