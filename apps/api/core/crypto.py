"""Reversible symmetric encryption for secrets we must read back in plaintext
(e.g. a per-org OpenAI API key) — unlike password hashing, which is
intentionally one-way, these need to be decrypted server-side to actually
call the provider.

Backed by Fernet (AES-128-CBC + HMAC, from the `cryptography` package,
already a dependency). The encryption key lives only in
``settings.secret_encryption_key`` (env var / secrets manager) — never in the
database next to the ciphertext it protects.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from apps.api.config import settings


class EncryptionNotConfigured(RuntimeError):
    """Raised when a secret needs encrypting/decrypting but no
    ``SECRET_ENCRYPTION_KEY`` is set in the environment."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = settings.secret_encryption_key
    if not key:
        raise EncryptionNotConfigured(
            "SECRET_ENCRYPTION_KEY is not set — cannot encrypt/decrypt org secrets. "
            "Generate one with `python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set it in the environment."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext``, returning an opaque token safe to store in the DB."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    """Decrypt a token produced by ``encrypt_secret``.

    Returns None (rather than raising) on a corrupt/foreign token or a key
    mismatch, so a caller that just needs "is there a usable secret here"
    can treat it the same as "not configured" instead of crashing the request.
    """
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def mask_secret(plaintext: str) -> str:
    """Last-4-visible preview for display, e.g. ``sk-...ab12`` — never enough
    to reconstruct the real key, just enough for a human to recognize it."""
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"sk-...{tail}"
