from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TemplateButtonSendParam(BaseModel):
    """Dynamic value for one URL/COPY_CODE template button — shared by every
    WhatsApp send path (single outbound send, campaigns, follow-up rules)."""

    index: int = Field(..., description="0-based position of this button in the template.")
    type: Literal["url", "copy_code"] = Field(
        ..., description="Which dynamic button kind this fills — matches the button's own type."
    )
    value: str = Field(..., description="The {{1}} URL suffix, or the coupon code to show.")
