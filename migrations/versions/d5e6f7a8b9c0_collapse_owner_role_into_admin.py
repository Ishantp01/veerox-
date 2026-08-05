"""collapse owner role into admin

Revision ID: d5e6f7a8b9c0
Revises: bcf7329a3fdb
Create Date: 2026-08-05 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'bcf7329a3fdb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ORG_MEMBERSHIP_ROLES dropped "owner": only ("admin", "member") remain.
    # Every existing owner membership becomes admin, the same privilege tier.
    op.execute("UPDATE org_memberships SET role = 'admin' WHERE role = 'owner'")


def downgrade() -> None:
    # Original owner/admin split can't be reconstructed once collapsed —
    # this is a one-way data migration.
    pass
