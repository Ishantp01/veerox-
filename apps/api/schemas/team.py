from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class TeamMemberOut(BaseModel):
    account_user_id: UUID
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    invited_at: datetime | None = None
    joined_at: datetime | None = None


class InviteMemberIn(BaseModel):
    email: EmailStr
    full_name: str | None = None
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
