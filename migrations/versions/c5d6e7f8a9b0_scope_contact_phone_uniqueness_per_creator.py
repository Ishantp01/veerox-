"""scope contact phone uniqueness per creator

Contact visibility is siloed by creator (see the prior migration,
b4c5d6e7f8a9), and each team member's contact list is meant to be
independent — rep A and rep B should each be able to have their own
contact for the same phone number (e.g. both talked to the same person),
and importing/adding a number someone else in the org already owns should
add it to *your* list rather than being blocked by their row. The old
(org_id, phone) constraint didn't allow that; this replaces it with
(org_id, phone, created_by_account_user_id).

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-04 13:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_contacts_org_phone', 'contacts', type_='unique')
    op.create_unique_constraint(
        'uq_contacts_org_phone_creator',
        'contacts',
        ['org_id', 'phone', 'created_by_account_user_id'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_contacts_org_phone_creator', 'contacts', type_='unique')
    op.create_unique_constraint('uq_contacts_org_phone', 'contacts', ['org_id', 'phone'])
