"""add contacts table and lead contact_id

Revision ID: 655bab7b4adb
Revises: 9c1e4a7d5f2b
Create Date: 2026-07-27 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '655bab7b4adb'
down_revision: Union[str, None] = '9c1e4a7d5f2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=32), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('company', sa.String(length=255), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('owner_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'phone', name='uq_contacts_org_phone'),
    )
    op.create_index('ix_contacts_org_id', 'contacts', ['org_id'])

    # Nullable, no backfill here by design — populating contact_id for
    # existing leads is a separate, re-runnable data migration (see
    # scripts/backfill_contacts.py), not bundled into this schema change.
    op.add_column(
        'leads',
        sa.Column('contact_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_leads_contact_id_contacts', 'leads', 'contacts', ['contact_id'], ['id'], ondelete='SET NULL'
    )
    op.create_index('ix_leads_contact_id', 'leads', ['contact_id'])


def downgrade() -> None:
    op.drop_index('ix_leads_contact_id', table_name='leads')
    op.drop_constraint('fk_leads_contact_id_contacts', 'leads', type_='foreignkey')
    op.drop_column('leads', 'contact_id')
    op.drop_index('ix_contacts_org_id', table_name='contacts')
    op.drop_table('contacts')
