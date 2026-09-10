"""add agent_connect_template_name to orgs

Lets an org pick, from the WhatsApp settings page, which approved template
the human-handoff notification (core/tools.py::transfer_to_human) sends.
NULL keeps the built-in behaviour: the hardcoded ``agent_connect_request``
once Meta-approved, else the ``appointment_confirmation`` fallback.

Revision ID: c1d2e3f4a5b6
Revises: b3d5c7e9f1a2
Create Date: 2026-09-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b3d5c7e9f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('agent_connect_template_name', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'agent_connect_template_name')
