"""add enabled_features and max_team_members to orgs

Lets a platform admin restrict which product modules an org can use
(deps.py's require_feature) and cap its team size (routers/team.py's
invite_member). NULL for every existing org == unrestricted/unlimited,
matching today's behavior until an admin sets either field.

Revision ID: dae24643f256
Revises: 8b323b795c46
Create Date: 2026-09-17 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'dae24643f256'
down_revision: Union[str, None] = '8b323b795c46'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('enabled_features', sa.JSON(), nullable=True))
    op.add_column('orgs', sa.Column('max_team_members', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'max_team_members')
    op.drop_column('orgs', 'enabled_features')
