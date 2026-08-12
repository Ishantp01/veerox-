"""add mobile to account_users

The admin login token is now SMS'd to the org admin on provisioning (see
apps/api/routers/auth.py's provision_org + channels/voice/plivo_client.send_sms)
in addition to being shown once in the dashboard, so we need somewhere to
keep the number it was sent to.

Revision ID: e5f6a7b8c9d0
Revises: d9e0f1a2b3c4
Create Date: 2026-08-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("account_users", sa.Column("mobile", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("account_users", "mobile")
