"""add orgs.conversation_voice_mode

Revision ID: b8d4f2a1c6e3
Revises: a7c3e1f2b4d6
Create Date: 2026-10-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d4f2a1c6e3'
down_revision: Union[str, None] = 'a7c3e1f2b4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and defaulted, so the previous release keeps running against the
    # migrated database. "shadow" = today's voice behaviour for orgs already on
    # human_conversation (log-only); nothing becomes live without an explicit change.
    op.add_column(
        'orgs',
        sa.Column(
            'conversation_voice_mode',
            sa.String(length=12),
            server_default='shadow',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('orgs', 'conversation_voice_mode')
