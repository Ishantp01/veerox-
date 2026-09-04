"""add scripts table

Voice-calling script library — an org can now have several named scripts
(see db/models/script.py), picked per campaign or left to fall back to
whichever one is_default. Purely additive: existing non-null orgs.script
values are backfilled here as each org's first ("Default") script, but
orgs.script itself is left untouched — it's still read by WhatsApp (see
core/agent.py::_system_prompt_for), which this change doesn't affect.

Revision ID: e7f8a9b0c1d2
Revises: c5d6e7f8a9b0
Create Date: 2026-09-04 13:10:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'c5d6e7f8a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scripts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_scripts_org_id', 'scripts', ['org_id'])

    # Backfill: each org's existing script override becomes its first
    # (and default) library entry.
    bind = op.get_bind()
    orgs = sa.table(
        'orgs',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('script', sa.Text),
    )
    scripts = sa.table(
        'scripts',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('name', sa.String),
        sa.column('content', sa.Text),
        sa.column('is_default', sa.Boolean),
    )
    rows = bind.execute(sa.select(orgs.c.id, orgs.c.script).where(orgs.c.script.isnot(None))).all()
    to_insert = [
        {'id': uuid.uuid4(), 'org_id': org_id, 'name': 'Default', 'content': script, 'is_default': True}
        for org_id, script in rows
        if script
    ]
    if to_insert:
        bind.execute(sa.insert(scripts), to_insert)


def downgrade() -> None:
    op.drop_index('ix_scripts_org_id', table_name='scripts')
    op.drop_table('scripts')
