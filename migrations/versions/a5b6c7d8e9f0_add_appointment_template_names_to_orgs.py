"""add appointment_confirmation_template_name and appointment_reminder_template_name to orgs

Lets an org pick, from the WhatsApp settings page, which approved templates
the immediate booking confirmation and the pre-appointment reminders
(core/tools.py::send_appointment_confirmation, schedule_appointment_reminders)
send. NULL on either keeps the built-in behaviour: the hardcoded
``appointment_confirmation``/``appointment_reminder`` templates.

Revision ID: a5b6c7d8e9f0
Revises: d3e4f5a6b7c8
Create Date: 2026-09-24 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('appointment_confirmation_template_name', sa.String(255), nullable=True))
    op.add_column('orgs', sa.Column('appointment_reminder_template_name', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'appointment_reminder_template_name')
    op.drop_column('orgs', 'appointment_confirmation_template_name')
