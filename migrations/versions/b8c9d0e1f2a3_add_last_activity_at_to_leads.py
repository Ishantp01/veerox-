"""add last_activity_at to leads

Revision ID: b8c9d0e1f2a3
Revises: a5b6c7d8e9f0
Create Date: 2026-09-25 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfilled from created_at so existing leads keep their current sort
    # position instead of all jumping to "now" — see
    # core/tools.py::get_or_create_lead_for_user for why this exists
    # (returning-customer leads need to bubble back to the top of the Leads
    # page instead of staying pinned to their original created_at).
    op.add_column(
        'leads',
        sa.Column(
            'last_activity_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True,
        ),
    )
    op.execute('UPDATE leads SET last_activity_at = created_at WHERE last_activity_at IS NULL')
    op.alter_column('leads', 'last_activity_at', nullable=False)
    op.create_index('ix_leads_last_activity_at', 'leads', ['last_activity_at'])


def downgrade() -> None:
    op.drop_index('ix_leads_last_activity_at', table_name='leads')
    op.drop_column('leads', 'last_activity_at')
