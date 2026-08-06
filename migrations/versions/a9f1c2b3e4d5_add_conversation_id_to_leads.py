"""add conversation_id to leads

Revision ID: a9f1c2b3e4d5
Revises: d5e6f7a8b9c0
Create Date: 2026-08-06 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9f1c2b3e4d5'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('conversation_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_leads_conversation_id_conversations',
        'leads',
        'conversations',
        ['conversation_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_leads_conversation_id_conversations', 'leads', type_='foreignkey')
    op.drop_column('leads', 'conversation_id')
