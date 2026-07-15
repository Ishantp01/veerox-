"""add channel to call_campaigns

Revision ID: 9c1e4a7d5f2b
Revises: 6d65c1c6b1f5
Create Date: 2026-07-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9c1e4a7d5f2b'
down_revision: Union[str, None] = '6d65c1c6b1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_campaigns',
        sa.Column('channel', sa.String(length=10), nullable=False, server_default='voice'),
    )


def downgrade() -> None:
    op.drop_column('call_campaigns', 'channel')
