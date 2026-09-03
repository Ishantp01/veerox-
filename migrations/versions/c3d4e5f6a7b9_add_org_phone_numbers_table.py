"""add org_phone_numbers table, drop orgs.plivo/twilio_phone_number

Replaces the single-Plivo-number/single-Twilio-number-per-org columns with a
child table so an org can have several dedicated numbers per provider (see
db/models/org_phone_number.py and channels/voice/org_numbers.py). Existing
non-null orgs.plivo_phone_number/twilio_phone_number values are backfilled
into org_phone_numbers as that provider's default (is_default=True) row
before the old columns are dropped.

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a7
Create Date: 2026-09-03 00:00:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c3d4e5f6a7b9'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'org_phone_numbers',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.String(length=10), nullable=False),
        sa.Column('phone_number', sa.String(length=32), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'phone_number', name='uq_org_phone_numbers_provider_number'),
    )
    op.create_index('ix_org_phone_numbers_org_id', 'org_phone_numbers', ['org_id'])

    # Backfill: each org's existing single number per provider becomes that
    # provider's default row.
    bind = op.get_bind()
    orgs = sa.table(
        'orgs',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('plivo_phone_number', sa.String),
        sa.column('twilio_phone_number', sa.String),
    )
    org_phone_numbers = sa.table(
        'org_phone_numbers',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('provider', sa.String),
        sa.column('phone_number', sa.String),
        sa.column('is_default', sa.Boolean),
    )
    rows = bind.execute(sa.select(orgs.c.id, orgs.c.plivo_phone_number, orgs.c.twilio_phone_number)).all()
    to_insert = []
    for org_id, plivo_number, twilio_number in rows:
        if plivo_number:
            to_insert.append(
                {
                    'id': uuid.uuid4(),
                    'org_id': org_id,
                    'provider': 'plivo',
                    'phone_number': plivo_number,
                    'is_default': True,
                }
            )
        if twilio_number:
            to_insert.append(
                {
                    'id': uuid.uuid4(),
                    'org_id': org_id,
                    'provider': 'twilio',
                    'phone_number': twilio_number,
                    'is_default': True,
                }
            )
    if to_insert:
        bind.execute(sa.insert(org_phone_numbers), to_insert)

    op.drop_constraint('uq_orgs_twilio_phone_number', 'orgs', type_='unique')
    op.drop_column('orgs', 'twilio_phone_number')
    op.drop_constraint('uq_orgs_plivo_phone_number', 'orgs', type_='unique')
    op.drop_column('orgs', 'plivo_phone_number')


def downgrade() -> None:
    op.add_column('orgs', sa.Column('plivo_phone_number', sa.String(length=32), nullable=True))
    op.create_unique_constraint('uq_orgs_plivo_phone_number', 'orgs', ['plivo_phone_number'])
    op.add_column('orgs', sa.Column('twilio_phone_number', sa.String(length=32), nullable=True))
    op.create_unique_constraint('uq_orgs_twilio_phone_number', 'orgs', ['twilio_phone_number'])

    bind = op.get_bind()
    org_phone_numbers = sa.table(
        'org_phone_numbers',
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('provider', sa.String),
        sa.column('phone_number', sa.String),
        sa.column('is_default', sa.Boolean),
    )
    orgs = sa.table(
        'orgs',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('plivo_phone_number', sa.String),
        sa.column('twilio_phone_number', sa.String),
    )
    rows = bind.execute(
        sa.select(org_phone_numbers.c.org_id, org_phone_numbers.c.provider, org_phone_numbers.c.phone_number)
        .where(org_phone_numbers.c.is_default.is_(True))
    ).all()
    for org_id, provider, phone_number in rows:
        column = orgs.c.plivo_phone_number if provider == 'plivo' else orgs.c.twilio_phone_number
        bind.execute(sa.update(orgs).where(orgs.c.id == org_id).values({column.name: phone_number}))

    op.drop_index('ix_org_phone_numbers_org_id', table_name='org_phone_numbers')
    op.drop_table('org_phone_numbers')
