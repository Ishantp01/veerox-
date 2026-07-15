"""add call recording fields to conversations

Revision ID: 13b875aaf5db
Revises: 049f3157716b
Create Date: 2026-07-14 17:47:25.424845

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '13b875aaf5db'
down_revision: Union[str, None] = '049f3157716b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('plivo_call_uuid', sa.String(length=64), nullable=True))
    op.add_column('conversations', sa.Column('recording_url', sa.String(length=500), nullable=True))
    op.add_column(
        'conversations', sa.Column('recording_duration_secs', sa.Float(), nullable=True)
    )
    op.create_index(
        'ix_conversations_plivo_call_uuid', 'conversations', ['plivo_call_uuid']
    )


def downgrade() -> None:
    op.drop_index('ix_conversations_plivo_call_uuid', table_name='conversations')
    op.drop_column('conversations', 'recording_duration_secs')
    op.drop_column('conversations', 'recording_url')
    op.drop_column('conversations', 'plivo_call_uuid')
