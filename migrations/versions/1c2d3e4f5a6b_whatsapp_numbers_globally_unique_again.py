"""whatsapp numbers globally unique across orgs again

Reverts b6c7d8e9f0a1: drops the partial unique index scoped to provider !=
'whatsapp' and restores a plain unique constraint on org_phone_numbers
(provider, phone_number) covering all providers, including whatsapp — a
Meta phone_number_id can no longer be attached to more than one org (see
db/models/org_phone_number.py and routers/billing.py::update_org, whose
app-level duplicate check now applies to provider="whatsapp" too).

Revision ID: 1c2d3e4f5a6b
Revises: e7308b93fde5
Create Date: 2026-09-17 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = '1c2d3e4f5a6b'
down_revision: Union[str, None] = 'e7308b93fde5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('uq_org_phone_numbers_provider_number', table_name='org_phone_numbers')
    op.create_unique_constraint(
        'uq_org_phone_numbers_provider_number',
        'org_phone_numbers',
        ['provider', 'phone_number'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_org_phone_numbers_provider_number', 'org_phone_numbers', type_='unique'
    )
    op.create_index(
        'uq_org_phone_numbers_provider_number',
        'org_phone_numbers',
        ['provider', 'phone_number'],
        unique=True,
        postgresql_where="provider != 'whatsapp'",
    )
