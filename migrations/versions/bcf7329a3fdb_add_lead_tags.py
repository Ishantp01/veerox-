"""add lead tags

Revision ID: bcf7329a3fdb
Revises: c4d5e6f7a8b9
Create Date: 2026-08-05 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bcf7329a3fdb'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'tags')
