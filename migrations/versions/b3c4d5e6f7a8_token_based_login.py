"""switch account_users from password to permanent login token, drop org_invites

Accounts are now only ever created by a platform admin
(POST /auth/provision-org) or an org owner/admin (POST /team/members) — no
self-registration, no invite-accept flow, so there's no password to hash and
no pending-invite state to track.

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('org_invites')
    # No existing password_hash values are meaningful login tokens, so this
    # is rename-then-backfill rather than a data-preserving migration —
    # every existing account needs a fresh token issued out-of-band by an
    # admin afterward.
    op.alter_column('account_users', 'password_hash', new_column_name='token_hash')
    op.create_unique_constraint('uq_account_users_token_hash', 'account_users', ['token_hash'])


def downgrade() -> None:
    op.drop_constraint('uq_account_users_token_hash', 'account_users', type_='unique')
    op.alter_column('account_users', 'token_hash', new_column_name='password_hash')
    op.create_table(
        'org_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('invited_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by_id'], ['account_users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
