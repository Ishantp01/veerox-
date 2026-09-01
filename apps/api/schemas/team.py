from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class TeamMemberOut(BaseModel):
    account_user_id: UUID
    email: str
    full_name: str | None = None
    # E.164 mobile number, if this teammate has one on file — see
    # core/tools.py's _resolve_team_notify_phone, which WhatsApp-notifies
    # this number when a lead asks to be connected to a human.
    mobile: str | None = None
    role: str
    is_active: bool
    invited_at: datetime | None = None
    joined_at: datetime | None = None
    # True for the org's original admin (created via /auth/provision-org, no
    # inviter) — excluded from the plan's max_seats count, so a limit of N
    # means "the owner plus up to N invited teammates", not N total.
    is_owner: bool = False


class InviteMemberIn(BaseModel):
    email: EmailStr
    full_name: str | None = None
    # E.164 mobile number. Optional — but without it, this teammate can't be
    # the one WhatsApp-notified on a human handoff (see
    # core/tools.py::_resolve_team_notify_phone).
    mobile: str | None = None
    role: str = "member"


class InviteMemberOut(BaseModel):
    account_user_id: UUID
    email: str
    role: str
    # Only set when a brand new AccountUser was created for this invite —
    # shown exactly once (see core/security.py). Omitted when the email
    # already belonged to an existing account and this invite just added a
    # membership onto their existing login.
    login_token: str | None = None


class UpdateMemberRoleIn(BaseModel):
    role: str


class RegenerateMemberTokenOut(BaseModel):
    account_user_id: UUID
    email: str
    # Shown exactly once — only the SHA-256 digest is stored server-side
    # (see core/security.py).
    login_token: str
