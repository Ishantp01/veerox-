"""add multi-tenant licensing tables and org deployment columns

Adds the central licence/organization-management schema (spec: centralized
licence management, organization provisioning, and feature allocation for
separate per-client deployments):

- orgs.central_org_ref / deployment_status / config_version /
  client_deployment_id — the owner-side source-of-truth identity and
  provisioning state for an org that may live in a separate client
  database. Existing orgs are backfilled with central_org_ref = id and
  deployment_status = "provisioned" (they're already running, just not
  through this new mechanism yet).
- client_deployments — one row per registered client backend, identified by
  the SHA-256 hash of its bearer token (core/deployment_auth.py) — never a
  recoverable secret.
- license_audit_events — full history behind Org's license_status/
  license_expires_at columns (which only ever hold the latest values).
- sync_events — durable record of pending organization/feature changes,
  applied by a client's own pull against GET /platform/sync/pending
  (core/provisioning_apply.py), never pushed by the owner.

Revision ID: 1fbd0c6f9ede
Revises: 72ab1cd1e18b
Create Date: 2026-09-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '1fbd0c6f9ede'
down_revision: Union[str, None] = '72ab1cd1e18b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'client_deployments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('config_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash', name='uq_client_deployments_token_hash'),
    )

    op.add_column('orgs', sa.Column('central_org_ref', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        'orgs',
        sa.Column('deployment_status', sa.String(length=30), nullable=False, server_default='pending_deployment'),
    )
    op.add_column('orgs', sa.Column('config_version', sa.Integer(), nullable=False, server_default='1'))
    op.add_column('orgs', sa.Column('client_deployment_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('orgs', sa.Column('setup_idempotency_key', sa.String(length=128), nullable=True))
    op.create_unique_constraint('uq_orgs_setup_idempotency_key', 'orgs', ['setup_idempotency_key'])
    op.create_foreign_key(
        'fk_orgs_client_deployment_id', 'orgs', 'client_deployments', ['client_deployment_id'], ['id'],
        ondelete='SET NULL',
    )

    # Backfill: every org that exists today is already running (through the
    # single owner deployment) — its central reference is its own id, and
    # it's already "provisioned" in the sense that matters (it has a working
    # database and application environment), just not through a registered
    # ClientDeployment row. New orgs created via POST /platform/organizations
    # from here on start "pending_deployment" instead (see Org model default).
    op.execute("UPDATE orgs SET central_org_ref = id, deployment_status = 'provisioned' WHERE central_org_ref IS NULL")
    op.alter_column('orgs', 'central_org_ref', nullable=False)
    op.create_unique_constraint('uq_orgs_central_org_ref', 'orgs', ['central_org_ref'])

    op.create_table(
        'license_audit_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('actor_account_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('previous_status', sa.String(length=20), nullable=True),
        sa.Column('new_status', sa.String(length=20), nullable=True),
        sa.Column('previous_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('new_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_account_user_id'], ['account_users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'sync_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('deployment_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('config_version', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['deployment_id'], ['client_deployments.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('sync_events')
    op.drop_table('license_audit_events')
    op.drop_constraint('uq_orgs_central_org_ref', 'orgs', type_='unique')
    op.drop_constraint('fk_orgs_client_deployment_id', 'orgs', type_='foreignkey')
    op.drop_constraint('uq_orgs_setup_idempotency_key', 'orgs', type_='unique')
    op.drop_column('orgs', 'setup_idempotency_key')
    op.drop_column('orgs', 'client_deployment_id')
    op.drop_column('orgs', 'config_version')
    op.drop_column('orgs', 'deployment_status')
    op.drop_column('orgs', 'central_org_ref')
    op.drop_table('client_deployments')
