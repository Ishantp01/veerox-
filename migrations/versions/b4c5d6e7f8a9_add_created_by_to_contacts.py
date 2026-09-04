"""add created_by_account_user_id to contacts

Contact visibility is now siloed by creator (routers/crm.py's list/get/
update/delete only ever see a contact whose created_by_account_user_id
matches the caller — every role, including admin, no org-wide exception;
see db/models/contact.py's docstring). Existing contacts predate this
column and have no recorded creator, so they're backfilled here to each
org's owner (the OrgMembership row with invited_by_id IS NULL — the account
that originally signed the org up) rather than left NULL, which would make
them invisible to everyone through the API even though the rows still
exist. An org with no resolvable owner membership (shouldn't happen, but
cheaper to allow for than assume against) keeps its contacts NULL.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-04 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'contacts',
        sa.Column('created_by_account_user_id', postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_contacts_created_by_account_user_id',
        'contacts',
        'account_users',
        ['created_by_account_user_id'],
        ['id'],
        ondelete='SET NULL',
    )

    bind = op.get_bind()
    contacts = sa.table(
        'contacts',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('created_by_account_user_id', postgresql.UUID(as_uuid=True)),
    )
    org_memberships = sa.table(
        'org_memberships',
        sa.column('org_id', postgresql.UUID(as_uuid=True)),
        sa.column('account_user_id', postgresql.UUID(as_uuid=True)),
        sa.column('invited_by_id', postgresql.UUID(as_uuid=True)),
    )
    owners = bind.execute(
        sa.select(org_memberships.c.org_id, org_memberships.c.account_user_id).where(
            org_memberships.c.invited_by_id.is_(None)
        )
    ).all()
    for org_id, owner_account_user_id in owners:
        bind.execute(
            sa.update(contacts)
            .where(contacts.c.org_id == org_id, contacts.c.created_by_account_user_id.is_(None))
            .values(created_by_account_user_id=owner_account_user_id)
        )


def downgrade() -> None:
    op.drop_constraint('fk_contacts_created_by_account_user_id', 'contacts', type_='foreignkey')
    op.drop_column('contacts', 'created_by_account_user_id')
