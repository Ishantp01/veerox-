"""add call_campaigns and campaign_targets

Revision ID: 6d65c1c6b1f5
Revises: 13b875aaf5db
Create Date: 2026-07-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '6d65c1c6b1f5'
down_revision: Union[str, None] = '13b875aaf5db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'call_campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'org_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('orgs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('criteria', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        'campaign_targets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'campaign_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('call_campaigns.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'org_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('orgs.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('qualified', sa.Boolean(), nullable=True),
        sa.Column('disposition_reason', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'conversation_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('conversations.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('called_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_campaign_targets_campaign_id', 'campaign_targets', ['campaign_id'])
    op.create_index('ix_campaign_targets_status', 'campaign_targets', ['status'])


def downgrade() -> None:
    op.drop_index('ix_campaign_targets_status', table_name='campaign_targets')
    op.drop_index('ix_campaign_targets_campaign_id', table_name='campaign_targets')
    op.drop_table('campaign_targets')
    op.drop_table('call_campaigns')
