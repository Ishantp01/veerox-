from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Shared by schemas/auth.py (ProvisionOrgIn), schemas/billing.py (OrgUpdateIn/
# OrgAdminOut), and schemas/admin.py (OrgNumbersIn/OrgNumbersOut) — the three
# places an org's dedicated Plivo/Twilio numbers are read or written. See
# channels/voice/org_numbers.py::replace_org_phone_numbers for how *In is
# applied and db/models/org_phone_number.py for the row shape *Out mirrors.


class OrgPhoneNumberIn(BaseModel):
    provider: Literal["plivo", "twilio"]
    phone_number: str = Field(..., description="E.164, e.g. +14155551234.")
    is_default: bool = Field(
        False,
        description="The number outbound calls dial from for this provider. "
        "If a provider has entries but none marked default, the first one "
        "becomes it; if more than one is marked, all but the first are demoted.",
    )


class OrgPhoneNumberOut(BaseModel):
    id: UUID
    provider: Literal["plivo", "twilio"]
    phone_number: str
    is_default: bool
    created_at: datetime
