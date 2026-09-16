"""add org plivo/twilio/meta channel credentials

Lets each org bring its own Plivo/Twilio account and Meta WhatsApp App
instead of dialing/sending through the platform's shared settings.plivo_*/
twilio_*/meta_* credentials (see apps/api/core/org_credentials.py). Secrets
are Fernet-encrypted at rest (apps/api/core/crypto.py); ids/sids are plain.
NULL for every existing org until set via PUT /admin/settings/{plivo,
twilio,meta}-credentials or the org-creation form.

Revision ID: a1c2d3e4f5b6
Revises: f010beb97c28
Create Date: 2026-09-16 00:00:01.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1c2d3e4f5b6'
down_revision: Union[str, None] = 'f010beb97c28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orgs', sa.Column('plivo_auth_id', sa.String(length=64), nullable=True))
    op.add_column('orgs', sa.Column('plivo_auth_token_encrypted', sa.Text(), nullable=True))
    op.add_column('orgs', sa.Column('twilio_account_sid', sa.String(length=64), nullable=True))
    op.add_column('orgs', sa.Column('twilio_auth_token_encrypted', sa.Text(), nullable=True))
    op.add_column('orgs', sa.Column('meta_app_id', sa.String(length=64), nullable=True))
    op.add_column('orgs', sa.Column('meta_app_secret_encrypted', sa.Text(), nullable=True))
    op.add_column(
        'orgs', sa.Column('meta_whatsapp_business_account_id', sa.String(length=64), nullable=True)
    )
    op.add_column('orgs', sa.Column('meta_verify_token_encrypted', sa.Text(), nullable=True))
    op.add_column('orgs', sa.Column('meta_access_token_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('orgs', 'meta_access_token_encrypted')
    op.drop_column('orgs', 'meta_verify_token_encrypted')
    op.drop_column('orgs', 'meta_whatsapp_business_account_id')
    op.drop_column('orgs', 'meta_app_secret_encrypted')
    op.drop_column('orgs', 'meta_app_id')
    op.drop_column('orgs', 'twilio_auth_token_encrypted')
    op.drop_column('orgs', 'twilio_account_sid')
    op.drop_column('orgs', 'plivo_auth_token_encrypted')
    op.drop_column('orgs', 'plivo_auth_id')
