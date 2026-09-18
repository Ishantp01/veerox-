"""add lead follow-up 2 and 3 fields

Follow up 1 keeps using the existing status/follow_up_at/follow_up_note
columns (just relabeled in the UI). These add two more independent
status+date+note follow-up slots on the same Lead row, replacing the old
"Review Stage" (qualification) card in the lead detail UI with "Follow up 2"
— qualification_status/_score/_notes columns are left alone since reports/
analytics/the CRM qualification page and the qualify_lead tool still read
and write them; this just stops exposing them as an editable card on the
lead detail page.

Revision ID: b8c9d0e1f2a3
Revises: 395af78986a5
Create Date: 2026-09-19 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = '395af78986a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('follow_up_2_status', sa.String(length=20), nullable=True))
    op.add_column('leads', sa.Column('follow_up_2_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('follow_up_2_note', sa.String(length=1000), nullable=True))
    op.add_column('leads', sa.Column('follow_up_3_status', sa.String(length=20), nullable=True))
    op.add_column('leads', sa.Column('follow_up_3_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('follow_up_3_note', sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'follow_up_3_note')
    op.drop_column('leads', 'follow_up_3_at')
    op.drop_column('leads', 'follow_up_3_status')
    op.drop_column('leads', 'follow_up_2_note')
    op.drop_column('leads', 'follow_up_2_at')
    op.drop_column('leads', 'follow_up_2_status')
