"""add payment_plans and razorpay_payments (independent of org license)

Revision ID: b3f1c2d4e5a6
Revises: a7c9e1f3b5d2
Create Date: 2026-11-13 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b3f1c2d4e5a6"
down_revision: str | None = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "razorpay_payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("payment_plans.id"), nullable=False),
        sa.Column("razorpay_order_id", sa.String(100), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(100), nullable=True),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_razorpay_payments_org_id", "razorpay_payments", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_razorpay_payments_org_id", table_name="razorpay_payments")
    op.drop_table("razorpay_payments")
    op.drop_table("payment_plans")
