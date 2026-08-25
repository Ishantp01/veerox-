from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

FollowUpTaskStatus = Literal["pending", "sending", "sent", "failed", "skipped", "cancelled"]

# Which leads a rule targets (matched against Lead.channel — see
# workers/follow_up_dispatcher.py's _materialize_rule_tasks). "voice" rules
# place an automated outbound call instead of sending WhatsApp — see
# _execute_task/_place_follow_up_call — so they need neither a message nor a
# template.
FollowUpRuleChannel = Literal["whatsapp", "voice"]


class FollowUpRuleCreate(BaseModel):
    name: str
    trigger_type: Literal["status_change"] = "status_change"
    trigger_config: dict[str, Any]
    channel: FollowUpRuleChannel = "whatsapp"
    message_template: str | None = None
    template_name: str | None = None
    template_language: str | None = None
    template_params: list[str] | None = None
    active: bool = True

    @model_validator(mode="after")
    def _require_template_or_message(self) -> FollowUpRuleCreate:
        if self.channel == "voice":
            return self
        if not self.template_name and not (self.message_template or "").strip():
            raise ValueError("Provide a WhatsApp template and/or a message")
        return self


class FollowUpRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    trigger_type: str
    trigger_config: dict[str, Any]
    channel: str
    message_template: str | None
    template_name: str | None = None
    template_language: str | None = None
    template_params: list[str] | None = None
    active: bool
    created_at: datetime


class FollowUpRuleUpdateIn(BaseModel):
    name: str | None = None
    trigger_config: dict[str, Any] | None = None
    message_template: str | None = None
    template_name: str | None = None
    template_language: str | None = None
    template_params: list[str] | None = None
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
