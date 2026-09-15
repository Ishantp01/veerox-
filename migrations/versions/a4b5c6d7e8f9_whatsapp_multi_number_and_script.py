"""whatsapp multi-number and multi-script support

Brings WhatsApp to parity with voice calling: an org can now have several
dedicated WhatsApp numbers (org_phone_numbers.provider='whatsapp', same
table Plivo/Twilio already use) and several WhatsApp scripts
(scripts.channel='whatsapp', same table voice scripts already use), with
per-campaign overrides (call_campaigns.whatsapp_number_id /
whatsapp_script_id) mirroring the existing voice-only phone_number_id /
script_id.

Existing non-null orgs.whatsapp_phone_number_id values are backfilled into
org_phone_numbers as that org's default (is_default=True) WhatsApp row
before the old column is dropped — same pattern as the plivo/twilio
single-column columns this table already replaced (see migration
c3d4e5f6a7b9). Existing scripts.* rows backfill to channel='voice' via the
column's server_default, so the existing voice library and any campaign's
script_id are unaffected.

Revision ID: a4b5c6d7e8f9
Revises: d3f5a8b1c2e4
Create Date: 2026-09-15 00:00:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, None] = 'd3f5a8b1c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'scripts',
        sa.Column('channel', sa.String(length=10), server_default='voice', nullable=False),
    )

    op.add_column(
        'call_campaigns', sa.Column('whatsapp_script_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_call_campaigns_whatsapp_script_id',
        'call_campaigns',
        'scripts',
        ['whatsapp_script_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.add_column(
        'call_campaigns', sa.Column('whatsapp_number_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_call_campaigns_whatsapp_number_id',
        'call_campaigns',
        'org_phone_numbers',
        ['whatsapp_number_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Backfill: each org's existing single WhatsApp number becomes its
    # default org_phone_numbers row for provider='whatsapp'.
    bind = op.get_bind()
    orgs = sa.table(
        'orgs',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('whatsapp_phone_number_id', sa.String),
    )
    org_phone_numbers = sa.table(
        'org_phone_numbers',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('provider', sa.String),
        sa.column('phone_number', sa.String),
        sa.column('is_default', sa.Boolean),
        sa.column('position', sa.Integer),
    )
    rows = bind.execute(
        sa.select(orgs.c.id, orgs.c.whatsapp_phone_number_id).where(
            orgs.c.whatsapp_phone_number_id.isnot(None)
        )
    ).all()

    # Dev/test data can have the same WhatsApp number set on more than one
    # org (e.g. several orgs pointed at the same Meta sandbox/test number) —
    # `uq_orgs_whatsapp_phone_number_id` only rejects that going forward, it
    # doesn't retroactively clean up rows that predate the constraint, and a
    # concurrent app instance's own startup seed (see db/startup_seed.py)
    # can also have already claimed a number for the default org before this
    # migration runs. `org_phone_numbers` keeps that same one-org-per-number
    # rule, so silently keep the FIRST org that claims each number here and
    # skip the rest rather than fail the whole migration.
    already_present = {
        phone_number
        for (phone_number,) in bind.execute(
            sa.select(org_phone_numbers.c.phone_number).where(
                org_phone_numbers.c.provider == 'whatsapp'
            )
        ).all()
    }
    to_insert = []
    for org_id, phone_number_id in rows:
        if not phone_number_id or phone_number_id in already_present:
            if phone_number_id:
                print(  # noqa: T201 — alembic migrations report via stdout
                    f"whatsapp_multi_number_and_script: skipping org {org_id}, "
                    f"number {phone_number_id} already claimed by another org"
                )
            continue
        already_present.add(phone_number_id)
        to_insert.append(
            {
                'id': uuid.uuid4(),
                'org_id': org_id,
                'provider': 'whatsapp',
                'phone_number': phone_number_id,
                'is_default': True,
                'position': 0,
            }
        )
    if to_insert:
        bind.execute(sa.insert(org_phone_numbers), to_insert)

    op.drop_constraint('uq_orgs_whatsapp_phone_number_id', 'orgs', type_='unique')
    op.drop_column('orgs', 'whatsapp_phone_number_id')


def downgrade() -> None:
    op.add_column('orgs', sa.Column('whatsapp_phone_number_id', sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        'uq_orgs_whatsapp_phone_number_id', 'orgs', ['whatsapp_phone_number_id']
    )

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
        sa.column('whatsapp_phone_number_id', sa.String),
    )
    rows = bind.execute(
        sa.select(org_phone_numbers.c.org_id, org_phone_numbers.c.phone_number).where(
            org_phone_numbers.c.provider == 'whatsapp', org_phone_numbers.c.is_default.is_(True)
        )
    ).all()
    for org_id, phone_number_id in rows:
        bind.execute(
            sa.update(orgs).where(orgs.c.id == org_id).values(whatsapp_phone_number_id=phone_number_id)
        )
    bind.execute(sa.delete(org_phone_numbers).where(org_phone_numbers.c.provider == 'whatsapp'))

    op.drop_constraint('fk_call_campaigns_whatsapp_number_id', 'call_campaigns', type_='foreignkey')
    op.drop_column('call_campaigns', 'whatsapp_number_id')
    op.drop_constraint('fk_call_campaigns_whatsapp_script_id', 'call_campaigns', type_='foreignkey')
    op.drop_column('call_campaigns', 'whatsapp_script_id')

    op.drop_column('scripts', 'channel')
