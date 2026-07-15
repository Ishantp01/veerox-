"""add channel column to leads

Revision ID: 3f9a7c1d2b44
Revises: 7abd1871474e
Create Date: 2026-07-14 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f9a7c1d2b44'
down_revision: Union[str, None] = '7abd1871474e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('channel', sa.String(length=16), nullable=True))
    op.create_index('ix_leads_channel', 'leads', ['channel'])

    # Best-effort backfill: inherit the channel of the user's most recently
    # started conversation. Rows for users with no conversation stay NULL.
    op.execute(
        """
        UPDATE leads
        SET channel = sub.channel
        FROM (
            SELECT DISTINCT ON (user_id) user_id, channel
            FROM conversations
            ORDER BY user_id, started_at DESC
        ) AS sub
        WHERE leads.user_id = sub.user_id AND leads.channel IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index('ix_leads_channel', table_name='leads')
    op.drop_column('leads', 'channel')
