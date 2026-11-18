"""add usage limits to payment_plans

Revision ID: c4a2d3e5f6b7
Revises: b3f1c2d4e5a6
Create Date: 2026-11-13 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4a2d3e5f6b7"
down_revision: str | None = "b3f1c2d4e5a6"
branch_labels = None
depends_on = None

COLUMNS = (
    "max_call_minutes",
    "max_whatsapp_messages",
    "max_team_members",
    "max_voice_campaigns",
    "max_whatsapp_campaigns",
)


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("payment_plans", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("payment_plans", name)
