from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Per-channel script library — one org can have several named scripts per
# channel, picked per campaign (see schemas/campaign.py's script_id /
# whatsapp_script_id) or left to fall back to whichever one is_default for
# that channel. Separate from schemas/admin.py's ScriptIn/ScriptOut, the
# legacy singleton override that's now WhatsApp's last-resort fallback only
# (see db/models/org.py's Org.script).

ScriptChannel = Literal["voice", "whatsapp"]


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    content: str
    channel: ScriptChannel
    is_default: bool
    # Which of the org's numbers this script is paired with, set from the
    # settings page. Doesn't affect which number a send goes OUT from, but
    # does pick the reply script for an inbound message on this number when
    # no campaign has pinned a more specific one (see db/models/script.py
    # and core/agent.py::_resolve_whatsapp_script_content). Only meaningful
    # for channel="whatsapp".
    phone_number_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ScriptCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    channel: ScriptChannel = "voice"
    is_default: bool = Field(
        False,
        description="Ignored (forced true) for an org's very first script on this "
        "channel — an org is never left with zero default scripts for a channel "
        "once it has at least one script there.",
    )
    phone_number_id: UUID | None = Field(
        None,
        description="Pair this script with one of the org's WhatsApp numbers — used as "
        "the reply script for messages on that number when no campaign has pinned a "
        "more specific one. Only valid for channel='whatsapp'.",
    )


class ScriptUpdateIn(BaseModel):
    # Rename/edit content only — setting the default is its own endpoint
    # (POST /admin/scripts/{id}/set-default) since it also has to unset the
    # org's previous default in the same transaction.
    name: str | None = Field(None, min_length=1, max_length=255)
    content: str | None = Field(None, min_length=1)
    phone_number_id: UUID | None = Field(
        None, description="Pair this script with one of the org's WhatsApp numbers, or "
        "null to unpair it."
    )
