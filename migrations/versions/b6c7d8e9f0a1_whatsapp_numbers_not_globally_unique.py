"""whatsapp numbers no longer globally unique across orgs

Drops the plain unique constraint on org_phone_numbers(provider,
phone_number) and replaces it with a partial unique index scoped to
provider != 'whatsapp' — Plivo/Twilio numbers still can't be assigned to two
orgs at once, but the same WhatsApp phone_number_id can now be attached to
more than one org (see db/models/org_phone_number.py and
routers/billing.py::update_org, whose app-level duplicate check is likewise
skipped for provider="whatsapp" now).

Revision ID: b6c7d8e9f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-09-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index('uq_org_phone_numbers_provider_number', table_name='org_phone_numbers')
    op.create_unique_constraint(
        'uq_org_phone_numbers_provider_number',
        'org_phone_numbers',
        ['provider', 'phone_number'],
    )
