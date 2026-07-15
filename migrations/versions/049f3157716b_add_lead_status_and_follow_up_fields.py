"""add lead status and follow-up fields

Revision ID: 049f3157716b
Revises: 3f9a7c1d2b44
Create Date: 2026-07-14 17:26:58.096401

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '049f3157716b'
down_revision: Union[str, None] = '3f9a7c1d2b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'leads',
        sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
    )
    op.add_column(
        'leads', sa.Column('follow_up_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'leads', sa.Column('follow_up_note', sa.String(length=1000), nullable=True)
    )
    op.create_index('ix_leads_status', 'leads', ['status'])


def downgrade() -> None:
    op.drop_index('ix_leads_status', table_name='leads')
    op.drop_column('leads', 'follow_up_note')
    op.drop_column('leads', 'follow_up_at')
    op.drop_column('leads', 'status')
