"""add created_by_account_user_id to call_campaigns

Records which account_user created a campaign. NULL for every pre-existing row
and for X-Admin-Token-created campaigns. A role=="member" caller only sees/acts
on campaigns where this is their id (see routers/admin.py::_member_lead_scope);
admins and platform superusers see all. Also decides who a campaign escalation
is routed to (see core/tools.py::transfer_to_human). Purely additive.

Revision ID: b3d5c7e9f1a2
Revises: a9b0c1d2e3f4
Create Date: 2026-09-07 13:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b3d5c7e9f1a2'
down_revision: Union[str, None] = 'a9b0c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_campaigns',
        sa.Column('created_by_account_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_call_campaigns_created_by_account_user_id',
        'call_campaigns',
        'account_users',
        ['created_by_account_user_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_call_campaigns_created_by_account_user_id', 'call_campaigns', type_='foreignkey'
    )
    op.drop_column('call_campaigns', 'created_by_account_user_id')
