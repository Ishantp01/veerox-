"""add callback_at to appointments

Records when a lead asked to be called back at a specific time (e.g. "I'm
busy, call me tomorrow at 5") instead of booking a real appointment —
extracted by the request_callback tool (core/tools.py) during a live
call/WhatsApp chat.

Revision ID: b1c2d3e4f5a6
Revises: c2d3e4f5a6b7
Create Date: 2026-09-24 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("appointments", "callback_at")
