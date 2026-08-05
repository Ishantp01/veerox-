from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr


class LoginIn(BaseModel):
    token: str


class SessionOut(BaseModel):
    token: str
    org_id: UUID
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False


class MeOut(BaseModel):
    org_id: UUID
    role: str
    account_user_id: UUID
    email: str
    full_name: str | None = None
    is_superuser: bool = False


class ProvisionOrgIn(BaseModel):
    org_name: str
    email: EmailStr
    full_name: str | None = None


class ProvisionOrgOut(BaseModel):
    org_id: UUID
    account_user_id: UUID
    email: str
    # Shown exactly once — only the SHA-256 digest is stored server-side
    # (see core/security.py), so this is the only chance to hand it to
    # whoever is provisioning the account.
    login_token: str
