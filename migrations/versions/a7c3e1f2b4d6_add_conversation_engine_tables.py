"""add conversation engine tables and orgs.conversation_mode

Revision ID: a7c3e1f2b4d6
Revises: 55361bf01ca3
Create Date: 2026-10-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a7c3e1f2b4d6'
down_revision: Union[str, None] = '55361bf01ca3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'orgs',
        sa.Column(
            'conversation_mode',
            sa.String(length=24),
            server_default='strict_script',
            nullable=False,
        ),
    )

    op.create_table(
        'conversation_turns',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('mode', sa.String(length=8), server_default='live', nullable=False),
        sa.Column('user_text', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=True),
        sa.Column('intent', sa.String(length=32), nullable=True),
        sa.Column('scenario', sa.String(length=32), nullable=True),
        sa.Column('emotion', sa.String(length=16), nullable=True),
        sa.Column('language', sa.String(length=16), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('source', sa.String(length=8), nullable=True),
        sa.Column('knowledge_ids', sa.JSON(), nullable=True),
        sa.Column('action_id', sa.String(length=32), nullable=True),
        sa.Column('action_result', sa.JSON(), nullable=True),
        sa.Column('reply_text', sa.Text(), nullable=True),
        sa.Column('fallback_reason', sa.String(length=32), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_conversation_turns_org_id', 'conversation_turns', ['org_id'])
    op.create_index('ix_conversation_turns_created_at', 'conversation_turns', ['created_at'])
    op.create_index(
        'ix_conversation_turns_org_conv_created',
        'conversation_turns',
        ['org_id', 'conversation_id', 'created_at'],
    )

    op.create_table(
        'conversation_memory',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope', sa.String(length=12), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'user_id', 'scope', 'key', name='uq_conversation_memory_fact'),
    )
    op.create_index('ix_conversation_memory_org_id', 'conversation_memory', ['org_id'])
    op.create_index('ix_conversation_memory_user_id', 'conversation_memory', ['user_id'])

    op.create_table(
        'knowledge_items',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('keywords', sa.Text(), nullable=True),
        sa.Column('language', sa.String(length=16), nullable=True),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False
        ),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_knowledge_items_org_id', 'knowledge_items', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_items_org_id', table_name='knowledge_items')
    op.drop_table('knowledge_items')
    op.drop_index('ix_conversation_memory_user_id', table_name='conversation_memory')
    op.drop_index('ix_conversation_memory_org_id', table_name='conversation_memory')
    op.drop_table('conversation_memory')
    op.drop_index('ix_conversation_turns_org_conv_created', table_name='conversation_turns')
    op.drop_index('ix_conversation_turns_created_at', table_name='conversation_turns')
    op.drop_index('ix_conversation_turns_org_id', table_name='conversation_turns')
    op.drop_table('conversation_turns')
    op.drop_column('orgs', 'conversation_mode')
