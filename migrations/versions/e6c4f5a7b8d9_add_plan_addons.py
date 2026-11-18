"""add is_addon flag to payment_plans

Revision ID: e6c4f5a7b8d9
Revises: c4a2d3e5f6b7
Create Date: 2026-11-18 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e6c4f5a7b8d9"
down_revision: str | None = "c4a2d3e5f6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_plans",
        sa.Column("is_addon", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("payment_plans", "is_addon")
