"""add appointments table

Revision ID: fec6191e5c81
Revises: ff01f483aa4d
Create Date: 2026-07-27 00:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'fec6191e5c81'
down_revision: Union[str, None] = 'ff01f483aa4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'appointments',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('contact_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), server_default='30', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='scheduled', nullable=False),
        sa.Column('assigned_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_appointments_org_id', 'appointments', ['org_id'])
    op.create_index('ix_appointments_scheduled_at', 'appointments', ['scheduled_at'])
    op.create_index('ix_appointments_status', 'appointments', ['status'])


def downgrade() -> None:
    op.drop_index('ix_appointments_status', table_name='appointments')
    op.drop_index('ix_appointments_scheduled_at', table_name='appointments')
    op.drop_index('ix_appointments_org_id', table_name='appointments')
    op.drop_table('appointments')
