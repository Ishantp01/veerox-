"""add usage metering, AWS cost allocation, and billing tables

Revision ID: a1b2c3d4e5f6
Revises: 72ab1cd1e18b
Create Date: 2026-09-28 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '55361bf01ca3'
down_revision: Union[str, None] = '72ab1cd1e18b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'usage_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_id', sa.String(length=255), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', sa.String(length=255), nullable=True),
        sa.Column('service', sa.String(length=20), nullable=False),
        sa.Column('usage_type', sa.String(length=40), nullable=False),
        sa.Column('quantity', sa.Numeric(24, 12), nullable=False),
        sa.Column('unit', sa.String(length=20), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_usage_events_event_id'),
    )
    op.create_index('ix_usage_events_event_id', 'usage_events', ['event_id'])
    op.create_index('ix_usage_events_org_created', 'usage_events', ['org_id', 'created_at'])
    op.create_index(
        'ix_usage_events_org_service_type_created',
        'usage_events',
        ['org_id', 'service', 'usage_type', 'created_at'],
    )

    op.create_table(
        'usage_daily',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('usage_date', sa.Date(), nullable=False),
        sa.Column('service', sa.String(length=20), nullable=False),
        sa.Column('usage_type', sa.String(length=40), nullable=False),
        sa.Column('total_quantity', sa.Numeric(24, 12), server_default='0', nullable=False),
        sa.Column('event_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('estimated_cost', sa.Numeric(14, 4), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'org_id', 'usage_date', 'service', 'usage_type', name='uq_usage_daily_org_date_service_type'
        ),
    )

    op.create_table(
        'usage_monthly',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_period', sa.String(length=7), nullable=False),
        sa.Column('service', sa.String(length=20), nullable=False),
        sa.Column('usage_type', sa.String(length=40), nullable=False),
        sa.Column('total_quantity', sa.Numeric(24, 12), server_default='0', nullable=False),
        sa.Column('allocated_aws_cost', sa.Numeric(14, 4), nullable=True),
        sa.Column('third_party_cost', sa.Numeric(14, 4), nullable=True),
        sa.Column('platform_fee', sa.Numeric(14, 4), nullable=True),
        sa.Column('total_estimated_charge', sa.Numeric(14, 4), nullable=True),
        sa.Column('calculation_status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('allocation_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'org_id', 'billing_period', 'service', 'usage_type',
            name='uq_usage_monthly_org_period_service_type',
        ),
    )

    op.create_table(
        'cost_rates',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('service', sa.String(length=20), nullable=False),
        sa.Column('usage_type', sa.String(length=40), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=True),
        sa.Column('unit_cost', sa.Numeric(14, 8), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'aws_cost_pools',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_period', sa.String(length=7), nullable=False),
        sa.Column('aws_account_id', sa.String(length=20), nullable=False),
        sa.Column('aws_service', sa.String(length=100), nullable=False),
        sa.Column('pool_category', sa.String(length=20), nullable=False),
        sa.Column('total_cost', sa.Numeric(14, 4), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('imported_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'billing_period', 'aws_account_id', 'aws_service',
            name='uq_aws_cost_pools_period_account_service',
        ),
    )

    op.create_table(
        'cost_allocations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_period', sa.String(length=7), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('pool_category', sa.String(length=20), nullable=False),
        sa.Column('allocated_cost', sa.Numeric(14, 4), nullable=False),
        sa.Column('metric_used', sa.String(length=40), nullable=False),
        sa.Column('metric_value', sa.Numeric(24, 12), server_default='0', nullable=False),
        sa.Column('total_metric_value', sa.Numeric(24, 12), server_default='0', nullable=False),
        sa.Column('is_estimated', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('allocation_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'billing_period', 'org_id', 'pool_category', 'allocation_version',
            name='uq_cost_allocations_period_org_category_version',
        ),
    )

    op.create_table(
        'invoices',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_period', sa.String(length=7), nullable=False),
        sa.Column('usage_charge', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('platform_fee', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('total_amount', sa.Numeric(14, 4), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='USD', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='draft', nullable=False),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'billing_period', name='uq_invoices_org_period'),
    )

    op.create_table(
        'usage_adjustments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_period', sa.String(length=7), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Numeric(14, 4), nullable=False),
        sa.Column('related_event_id', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_account_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_account_user_id'], ['account_users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('usage_adjustments')
    op.drop_table('invoices')
    op.drop_table('cost_allocations')
    op.drop_table('aws_cost_pools')
    op.drop_table('cost_rates')
    op.drop_table('usage_monthly')
    op.drop_table('usage_daily')
    op.drop_index('ix_usage_events_org_service_type_created', table_name='usage_events')
    op.drop_index('ix_usage_events_org_created', table_name='usage_events')
    op.drop_index('ix_usage_events_event_id', table_name='usage_events')
    op.drop_table('usage_events')
