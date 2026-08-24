"""add plan resource_type and org resource_limits

Lets a Plan row represent either a full subscription bundle
(resource_type NULL, unchanged legacy behavior) or a single-resource
recharge/top-up SKU (resource_type set to one of
apps.api.db.models.plan.PLAN_RESOURCE_TYPES). Org.resource_limits is the
org's effective per-resource limits once a recharge has touched it,
overriding the catalog Plan.limits key-by-key so a recharge can top up one
resource without resetting the other three (see routers/billing.py
`_activate_paid_payment`, deps.py `effective_limits`).

Both columns are nullable with no backfill: NULL reproduces today's
behavior exactly (org.resource_limits is None -> fall back to plan.limits
wholesale; plan.resource_type is None -> a full plan, as before).

Revision ID: b5c6d7e8f9a0
Revises: a2b3c4d5e6f7
Create Date: 2026-08-24 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('plans', sa.Column('resource_type', sa.String(length=30), nullable=True))
    op.add_column('orgs', sa.Column('resource_limits', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'resource_limits')
    op.drop_column('plans', 'resource_type')
