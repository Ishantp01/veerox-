"""add deal_value to leads

Revision ID: 5d9cd5c00fd6
Revises: a152273213e2
Create Date: 2026-07-27 00:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5d9cd5c00fd6'
down_revision: Union[str, None] = 'a152273213e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('deal_value', sa.Numeric(precision=12, scale=2), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'deal_value')
