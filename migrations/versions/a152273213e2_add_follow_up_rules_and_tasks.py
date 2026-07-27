"""add follow up rules and tasks

Revision ID: a152273213e2
Revises: fec6191e5c81
Create Date: 2026-07-27 00:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a152273213e2'
down_revision: Union[str, None] = 'fec6191e5c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'follow_up_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('trigger_type', sa.String(length=30), server_default='status_change', nullable=False),
        sa.Column('trigger_config', sa.JSON(), nullable=False),
        sa.Column('channel', sa.String(length=16), server_default='whatsapp', nullable=False),
        sa.Column('message_template', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'follow_up_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rule_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['rule_id'], ['follow_up_rules.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_follow_up_tasks_status', 'follow_up_tasks', ['status'])
    op.create_index('ix_follow_up_tasks_run_at', 'follow_up_tasks', ['run_at'])
    op.create_index('ix_follow_up_tasks_lead_id', 'follow_up_tasks', ['lead_id'])


def downgrade() -> None:
    op.drop_index('ix_follow_up_tasks_lead_id', table_name='follow_up_tasks')
    op.drop_index('ix_follow_up_tasks_run_at', table_name='follow_up_tasks')
    op.drop_index('ix_follow_up_tasks_status', table_name='follow_up_tasks')
    op.drop_table('follow_up_tasks')
    op.drop_table('follow_up_rules')
