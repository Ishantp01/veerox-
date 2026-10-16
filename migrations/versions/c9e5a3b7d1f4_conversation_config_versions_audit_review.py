"""scenario config versions, audit events, turn review + config columns

Revision ID: c9e5a3b7d1f4
Revises: b8d4f2a1c6e3
Create Date: 2026-10-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9e5a3b7d1f4'
down_revision: Union[str, None] = 'b8d4f2a1c6e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All additive (new tables, nullable columns), so the previous release keeps
    # running against the migrated database.
    op.create_table(
        'conversation_config_versions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('rolled_back_from', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.String(length=120), nullable=True),
        sa.Column('published_by', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'version', name='uq_conversation_config_version'),
    )
    op.create_index('ix_conversation_config_versions_org_id', 'conversation_config_versions', ['org_id'])

    op.create_table(
        'conversation_audit_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('actor', sa.String(length=120), nullable=False),
        sa.Column('action', sa.String(length=48), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=False),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_conversation_audit_events_org_id', 'conversation_audit_events', ['org_id'])
    op.create_index('ix_conversation_audit_org_created', 'conversation_audit_events', ['org_id', 'created_at'])

    op.add_column('conversation_turns', sa.Column('config_version_id', sa.Uuid(), nullable=True))
    op.add_column('conversation_turns', sa.Column('config_version', sa.Integer(), nullable=True))
    op.add_column('conversation_turns', sa.Column('review_label', sa.String(length=10), nullable=True))
    op.add_column('conversation_turns', sa.Column('review_scenario', sa.String(length=32), nullable=True))
    op.add_column('conversation_turns', sa.Column('reviewed_by', sa.String(length=120), nullable=True))
    op.add_column('conversation_turns', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_conversation_turns_config_version',
        'conversation_turns',
        'conversation_config_versions',
        ['config_version_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_conversation_turns_config_version', 'conversation_turns', type_='foreignkey')
    for column in (
        'reviewed_at', 'reviewed_by', 'review_scenario', 'review_label', 'config_version', 'config_version_id',
    ):
        op.drop_column('conversation_turns', column)
    op.drop_index('ix_conversation_audit_org_created', table_name='conversation_audit_events')
    op.drop_index('ix_conversation_audit_events_org_id', table_name='conversation_audit_events')
    op.drop_table('conversation_audit_events')
    op.drop_index('ix_conversation_config_versions_org_id', table_name='conversation_config_versions')
    op.drop_table('conversation_config_versions')
