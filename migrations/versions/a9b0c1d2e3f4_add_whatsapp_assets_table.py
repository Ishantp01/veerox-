"""add whatsapp_assets table

Org-owned media library for the WhatsApp agent — an org uploads files (a
price list, a brochure, a short video) and the agent sends the right one to
a contact who asks for that information, via the new ``send_whatsapp_file``
tool (see core/tools.py, core/whatsapp_assets.py). Bytes are stored in the
row; Meta fetches the file from the public
``/media/wa-asset/{id}?k={access_key}`` route. Purely additive.

Revision ID: a9b0c1d2e3f4
Revises: f7a8b9c0d1e2
Create Date: 2026-09-07 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'a9b0c1d2e3f4'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'whatsapp_assets',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('media_type', sa.String(length=16), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=128), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('access_key', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_whatsapp_assets_org_id', 'whatsapp_assets', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_whatsapp_assets_org_id', table_name='whatsapp_assets')
    op.drop_table('whatsapp_assets')
