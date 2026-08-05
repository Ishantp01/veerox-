"""Login credentials for AccountUser: a permanent random token, not a
password. Accounts are created by a platform admin (POST /auth/provision-org)
or by an org admin inviting a teammate (POST /team/members) — there is
no public self-registration, so there's no low-entropy human-chosen secret to
protect against offline brute force. That means the slow, adaptive hashing
password schemes need (argon2/bcrypt) is unnecessary overhead here; a plain
SHA-256 digest of the high-entropy token is enough to avoid storing it in
plaintext while still allowing an indexed equality lookup on login.
"""

from __future__ import annotations

import hashlib
import secrets


def generate_login_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
