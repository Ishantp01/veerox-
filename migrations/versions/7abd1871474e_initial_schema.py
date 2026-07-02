"""initial schema

Revision ID: 7abd1871474e
Revises: 1293c6321767
Create Date: 2026-05-29 12:16:05.619768

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7abd1871474e'
down_revision: Union[str, None] = '1293c6321767'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Duplicate initial schema revision kept as a no-op so existing revision
    # history can advance without attempting to recreate tables.
    pass


def downgrade() -> None:
    pass
