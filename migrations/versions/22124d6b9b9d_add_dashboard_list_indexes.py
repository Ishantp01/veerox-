"""add dashboard list indexes

Revision ID: 22124d6b9b9d
Revises: a1b2c3d4e5f6
Create Date: 2026-08-13 19:10:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '22124d6b9b9d'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # messages.conversation_id had no index at all despite being grouped/
    # joined on for every /admin/conversations page load (message-count
    # subquery). leads.created_at and conversations.started_at/org_id are
    # sorted/filtered on for every list page load and were also unindexed.
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('ix_leads_created_at', 'leads', ['created_at'])
    op.create_index('ix_conversations_started_at', 'conversations', ['started_at'])
    op.create_index('ix_conversations_org_id', 'conversations', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_conversations_org_id', table_name='conversations')
    op.drop_index('ix_conversations_started_at', table_name='conversations')
    op.drop_index('ix_leads_created_at', table_name='leads')
    op.drop_index('ix_messages_conversation_id', table_name='messages')
