"""add org voice greeting

Revision ID: e2a4c6d8f0b1
Revises: d0f6b4c8e2a5
Create Date: 2026-10-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2a4c6d8f0b1'
down_revision: Union[str, None] = 'd0f6b4c8e2a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and nullable: existing orgs keep the default greeting (NULL).
    op.add_column('orgs', sa.Column('voice_greeting', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'voice_greeting')
