"""rename plans.stripe_price_id to razorpay_plan_id

Revision ID: f4d5e6a7b8c9
Revises: e3c4d5f6a7b8
Create Date: 2026-07-31 14:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'f4d5e6a7b8c9'
down_revision: Union[str, None] = 'e3c4d5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column has never held data (billing wasn't wired to a provider yet), so
    # a straight rename is safe rather than add-then-backfill-then-drop.
    op.alter_column('plans', 'stripe_price_id', new_column_name='razorpay_plan_id')


def downgrade() -> None:
    op.alter_column('plans', 'razorpay_plan_id', new_column_name='stripe_price_id')
