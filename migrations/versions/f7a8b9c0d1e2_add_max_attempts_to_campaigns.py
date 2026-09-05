"""add max_attempts to call_campaigns

Per-campaign dial-attempt cap for the voice dialer (apps/api/workers/
campaign_dialer.py). Previously a hard-coded ``_MAX_ATTEMPTS = 3``; now
selectable per campaign at creation time (1-5). Voice-only — the WhatsApp
dispatcher keeps its own constant. Defaults to 3 so existing campaigns and
every non-voice entry point behave exactly as before.

Revision ID: f7a8b9c0d1e2
Revises: d6e7f8a9b0c1
Create Date: 2026-09-04 13:40:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_campaigns',
        sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('call_campaigns', 'max_attempts')
