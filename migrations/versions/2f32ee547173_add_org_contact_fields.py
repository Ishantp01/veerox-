"""add orgs.contact_name/contact_email/contact_mobile

Plain contact-person info the platform admin can record and edit for ANY
org, regardless of hosting type — distinct from a real admin login
(AccountUser + OrgMembership), which only exists for an org running on the
shared platform. An org set up on its own separate server has no such
account in the owner's database (see docs/multi-tenant-licensing.md), so
these columns are the fallback place to record who to contact about it.

Revision ID: 2f32ee547173
Revises: 1fbd0c6f9ede
Create Date: 2026-09-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2f32ee547173'
down_revision: Union[str, None] = '1fbd0c6f9ede'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('contact_name', sa.String(length=255), nullable=True))
    op.add_column('orgs', sa.Column('contact_email', sa.String(length=255), nullable=True))
    op.add_column('orgs', sa.Column('contact_mobile', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'contact_mobile')
    op.drop_column('orgs', 'contact_email')
    op.drop_column('orgs', 'contact_name')
