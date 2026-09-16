from __future__ import annotations

import pytest

from apps.api.core import crypto


def test_encrypt_decrypt_round_trips() -> None:
    ciphertext = crypto.encrypt_secret("sk-test-abcd1234")
    assert ciphertext != "sk-test-abcd1234"
    assert crypto.decrypt_secret(ciphertext) == "sk-test-abcd1234"


def test_decrypt_returns_none_for_garbage_token() -> None:
    assert crypto.decrypt_secret("not-a-real-fernet-token") is None


def test_decrypt_returns_none_under_a_different_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    from apps.api.config import settings as settings_module

    ciphertext = crypto.encrypt_secret("sk-test-abcd1234")
    monkeypatch.setattr(settings_module, "secret_encryption_key", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    try:
        assert crypto.decrypt_secret(ciphertext) is None
    finally:
        crypto._fernet.cache_clear()


def test_mask_secret_shows_only_last_four_chars() -> None:
    assert crypto.mask_secret("sk-test-abcd1234") == "sk-...1234"
    assert crypto.mask_secret("ab") == "sk-...ab"


def test_encrypt_raises_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api.config import settings as settings_module

    monkeypatch.setattr(settings_module, "secret_encryption_key", None)
    crypto._fernet.cache_clear()
    try:
        with pytest.raises(crypto.EncryptionNotConfigured):
            crypto.encrypt_secret("sk-test-abcd1234")
    finally:
        crypto._fernet.cache_clear()
