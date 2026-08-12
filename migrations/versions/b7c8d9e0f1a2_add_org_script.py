"""add script to orgs

Lets an org author their own version of the outbound call/WhatsApp script
(apps/api/core/prompts.py's OUTBOUND_CALL_PROMPT), editable from the
dashboard settings page. NULL keeps the platform default script.

Revision ID: b7c8d9e0f1a2
Revises: a9f1c2b3e4d5
Create Date: 2026-08-08 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'a9f1c2b3e4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('script', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'script')
