"""add plans table and org plan/billing_status columns

Revision ID: d2b3c4e5f6a7
Revises: c1a2b3d4e5f6
Create Date: 2026-07-31 00:05:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd2b3c4e5f6a7'
down_revision: Union[str, None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BASIC_PLAN_ID = '00000000-0000-0000-0000-0000000000b1'
_PRO_PLAN_ID = '00000000-0000-0000-0000-0000000000b2'
_PREMIUM_PLAN_ID = '00000000-0000-0000-0000-0000000000b3'


def upgrade() -> None:
    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('price_cents_monthly', sa.Integer(), nullable=False),
        sa.Column('stripe_price_id', sa.String(length=100), nullable=True),
        sa.Column('limits', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.execute(
        f"""
        INSERT INTO plans (id, code, name, price_cents_monthly, limits, is_active, created_at)
        VALUES
            ('{_BASIC_PLAN_ID}', 'basic', 'Basic', 0,
             '{{"max_seats": 1, "max_campaigns": 3, "max_leads_per_month": 500,
                "max_tokens_per_month": 100000, "max_call_minutes_per_month": 60,
                "max_whatsapp_messages_per_month": 200}}', true, now()),
            ('{_PRO_PLAN_ID}', 'pro', 'Pro', 490000,
             '{{"max_seats": 5, "max_campaigns": 20, "max_leads_per_month": 5000,
                "max_tokens_per_month": 2000000, "max_call_minutes_per_month": 1000,
                "max_whatsapp_messages_per_month": 5000}}', true, now()),
            ('{_PREMIUM_PLAN_ID}', 'premium', 'Premium', 1490000,
             '{{"max_seats": 25, "max_campaigns": 100, "max_leads_per_month": 50000,
                "max_tokens_per_month": 20000000, "max_call_minutes_per_month": 10000,
                "max_whatsapp_messages_per_month": 50000}}', true, now())
        """
    )

    op.add_column('orgs', sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        'orgs',
        sa.Column('billing_status', sa.String(length=20), server_default='trialing', nullable=False),
    )
    op.create_foreign_key('fk_orgs_plan_id', 'orgs', 'plans', ['plan_id'], ['id'])

    # Backfill existing org(s) onto the basic plan.
    op.execute(f"UPDATE orgs SET plan_id = '{_BASIC_PLAN_ID}' WHERE plan_id IS NULL")


def downgrade() -> None:
    op.drop_constraint('fk_orgs_plan_id', 'orgs', type_='foreignkey')
    op.drop_column('orgs', 'billing_status')
    op.drop_column('orgs', 'plan_id')
    op.drop_table('plans')
