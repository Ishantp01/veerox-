"""rename lead intent 'escalation' to 'human_support'

Revision ID: a1c2e3f4b5d6
Revises: d2e3f4a5b6c8
Create Date: 2026-09-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'a1c2e3f4b5d6'
down_revision: Union[str, None] = 'd2e3f4a5b6c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE leads SET intent = 'human_support' WHERE intent = 'escalation'")


def downgrade() -> None:
    op.execute("UPDATE leads SET intent = 'escalation' WHERE intent = 'human_support'")
