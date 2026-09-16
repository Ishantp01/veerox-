"""add org license fields and drop razorpay billing/plan tables

Replaces the Razorpay-based plan/billing system with a manually
admin-managed org license (see docs/razorpay-billing-removal.md): the
platform admin issues/renews/extends/suspends/reactivates a license per
org, with a validity/expiry date, entirely outside any payment gateway.

Existing orgs are backfilled active with no expiry set — the admin issues
each one's first real license via POST /billing/orgs/{id}/license/issue.

Revision ID: 1a30b485bdee
Revises: a1c2d3e4f5b6
Create Date: 2026-09-16 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '1a30b485bdee'
down_revision: Union[str, None] = 'a1c2d3e4f5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'orgs',
        sa.Column('license_status', sa.String(length=20), server_default='active', nullable=False),
    )
    op.add_column('orgs', sa.Column('license_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('license_issued_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('license_notes', sa.Text(), nullable=True))

    op.drop_table('billing_payments')
    op.drop_table('billing_events')

    op.drop_constraint('fk_orgs_plan_id', 'orgs', type_='foreignkey')
    op.drop_column('orgs', 'plan_id')
    op.drop_column('orgs', 'billing_status')
    op.drop_column('orgs', 'plan_started_at')
    op.drop_column('orgs', 'resource_limits')
    op.drop_column('orgs', 'free_plan_claimed_at')

    op.drop_table('plans')


def downgrade() -> None:
    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('price_cents', sa.Integer(), nullable=False),
        sa.Column('limits', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('resource_type', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    op.add_column('orgs', sa.Column('free_plan_claimed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orgs', sa.Column('resource_limits', sa.JSON(), nullable=True))
    op.add_column('orgs', sa.Column('plan_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'orgs',
        sa.Column('billing_status', sa.String(length=20), server_default='trialing', nullable=False),
    )
    op.add_column('orgs', sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_orgs_plan_id', 'orgs', 'plans', ['plan_id'], ['id'])

    op.create_table(
        'billing_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('provider_event_id', sa.String(length=100), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_event_id'),
    )
    op.create_table(
        'billing_payments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('provider_order_id', sa.String(length=100), nullable=False),
        sa.Column('provider_payment_id', sa.String(length=100), nullable=True),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_order_id'),
    )

    op.drop_column('orgs', 'license_notes')
    op.drop_column('orgs', 'license_issued_at')
    op.drop_column('orgs', 'license_expires_at')
    op.drop_column('orgs', 'license_status')
