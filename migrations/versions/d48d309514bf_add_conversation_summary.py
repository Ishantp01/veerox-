"""add conversation summary

Revision ID: d48d309514bf
Revises: 5d9cd5c00fd6
Create Date: 2026-07-27 01:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd48d309514bf'
down_revision: Union[str, None] = '5d9cd5c00fd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('conversations', sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('conversations', 'summary')
