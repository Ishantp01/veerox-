"""add channel to campaign_targets

Moves channel routing from CallCampaign (one value for the whole campaign)
down to CampaignTarget (one value per contact), so a single upload can
produce ONE campaign holding both voice and WhatsApp targets instead of two
separate campaigns split by channel.

Existing rows are backfilled from their parent call_campaigns.channel — the
value that actually determined their routing until now — not a blanket
default, since defaulting everything to "voice" would silently stop every
existing WhatsApp campaign's dispatcher from finding its own targets the
moment the new code (which reads CampaignTarget.channel instead of
CallCampaign.channel) starts running.

CallCampaign.channel is unchanged as a column but becomes display-only after
this — see call_campaign.py's updated comment.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-08-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable add first — nothing is assumed "voice" before the backfill
    # below runs against each row's actual parent-campaign channel.
    op.add_column('campaign_targets', sa.Column('channel', sa.String(length=10), nullable=True))

    op.execute(
        """
        UPDATE campaign_targets
        SET channel = call_campaigns.channel
        FROM call_campaigns
        WHERE campaign_targets.campaign_id = call_campaigns.id
          AND campaign_targets.channel IS NULL
        """
    )

    op.alter_column('campaign_targets', 'channel', nullable=False, server_default='voice')
    op.create_index('ix_campaign_targets_channel', 'campaign_targets', ['channel'])


def downgrade() -> None:
    op.drop_index('ix_campaign_targets_channel', table_name='campaign_targets')
    op.drop_column('campaign_targets', 'channel')
