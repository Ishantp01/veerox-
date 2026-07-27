from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

FollowUpTaskStatus = Literal["pending", "sending", "sent", "failed", "skipped", "cancelled"]


class FollowUpRuleCreate(BaseModel):
    name: str
    trigger_type: Literal["status_change"] = "status_change"
    trigger_config: dict[str, Any]
    channel: str = "whatsapp"
    message_template: str
    active: bool = True


class FollowUpRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    trigger_type: str
    trigger_config: dict[str, Any]
    channel: str
    message_template: str
    active: bool
    created_at: datetime


class FollowUpRuleUpdateIn(BaseModel):
    name: str | None = None
    trigger_config: dict[str, Any] | None = None
    message_template: str | None = None
    active: bool | None = None


class FollowUpTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    lead_id: UUID
    rule_id: UUID | None
    run_at: datetime
    status: FollowUpTaskStatus
    created_at: datetime
    sent_at: datetime | None
