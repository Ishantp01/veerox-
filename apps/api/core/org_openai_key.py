"""Resolve which OpenAI API key to use for a given org.

An org with its own key (set via PUT /admin/settings/openai-key, see
routers/admin.py) uses that; otherwise every call site falls back to the
platform's shared ``settings.openai_api_key``, unchanged from before this
existed.
"""

from __future__ import annotations

from apps.api.config import settings
from apps.api.core.crypto import decrypt_secret
from apps.api.db.models.org import Org


def resolve_openai_api_key(org: Org | None) -> str | None:
    """The API key to use for this org's OpenAI calls.

    Falls back to the platform key when the org has none configured, or
    when its stored ciphertext fails to decrypt (wrong/rotated
    SECRET_ENCRYPTION_KEY) — same as "not configured" rather than an error,
    since calls must keep working on the platform key either way.
    """
    if org is not None and org.openai_api_key_encrypted:
        decrypted = decrypt_secret(org.openai_api_key_encrypted)
        if decrypted:
            return decrypted
    return settings.openai_api_key
