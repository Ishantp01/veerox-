"""split follow_ups org feature flag into follow_ups_voice/follow_ups_whatsapp

AVAILABLE_ORG_FEATURES (db/models/org.py) no longer has a single "follow_ups"
key — Automated Follow-ups is now two sidebar items/pages, one per channel,
each gated by its own key. Any org whose Org.enabled_features already
contained "follow_ups" gets both new keys added in its place, so existing
orgs keep exactly the access they had (both channels), rather than silently
losing follow-ups access. Orgs with enabled_features IS NULL (unrestricted,
the default) are untouched.

Revision ID: c2d3e4f5a6b7
Revises: e5f6a7b8aabb
Create Date: 2026-09-24 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql

revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'e5f6a7b8aabb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_orgs = sa.table(
    "orgs",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("enabled_features", sa.JSON),
)


def upgrade() -> None:
    bind = op.get_bind()
    session = orm.Session(bind=bind)
    rows = session.execute(
        sa.select(_orgs.c.id, _orgs.c.enabled_features).where(
            _orgs.c.enabled_features.isnot(None)
        )
    ).all()
    for org_id, features in rows:
        if not features or "follow_ups" not in features:
            continue
        new_features = [f for f in features if f != "follow_ups"]
        for key in ("follow_ups_voice", "follow_ups_whatsapp"):
            if key not in new_features:
                new_features.append(key)
        session.execute(
            _orgs.update().where(_orgs.c.id == org_id).values(enabled_features=new_features)
        )
    session.commit()


def downgrade() -> None:
    bind = op.get_bind()
    session = orm.Session(bind=bind)
    rows = session.execute(
        sa.select(_orgs.c.id, _orgs.c.enabled_features).where(
            _orgs.c.enabled_features.isnot(None)
        )
    ).all()
    for org_id, features in rows:
        if not features:
            continue
        has_voice = "follow_ups_voice" in features
        has_whatsapp = "follow_ups_whatsapp" in features
        if not has_voice and not has_whatsapp:
            continue
        new_features = [
            f for f in features if f not in ("follow_ups_voice", "follow_ups_whatsapp")
        ]
        if "follow_ups" not in new_features:
            new_features.append("follow_ups")
        session.execute(
            _orgs.update().where(_orgs.c.id == org_id).values(enabled_features=new_features)
        )
    session.commit()
