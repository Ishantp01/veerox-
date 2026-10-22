"""P0 logging regressions: synthetic data, including nested provider variants."""
from __future__ import annotations

import json
import io
import logging

import structlog

import pytest

from apps.api.config import settings
from apps.api.core.privacy import privacy_processor, redact_text

SECRET = "synthetic-private-credential"
SPEECH = "SYNTHETIC-PRIVATE-SPEECH"
PHONE = "+919876543210"


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql", "redis", "rediss", "https"])
def test_connection_url_passwords_and_encoded_query_values_are_withheld(scheme):
    value = f"{scheme}://offline:{SECRET}@localhost:1234/pilot?token={SECRET}&caller=%2B919876543210"
    result = redact_text(value)
    assert SECRET not in result and "9876543210" not in result
    assert "localhost:1234/pilot" in result


@pytest.mark.parametrize("key", ["Authorization", "X-Api-Key", "xi_api_key", "password", "openai_api_key", "clientSecret"])
def test_nested_credential_fields_are_withheld(monkeypatch, key):
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    event = {"event": "failure", "diagnostic": [{"nested": {key: SECRET}}]}
    assert SECRET not in json.dumps(privacy_processor(None, "warning", event))


@pytest.mark.parametrize("key", ["body", "payload", "params", "headers", "request", "response", "provider_payload"])
def test_raw_payloads_are_never_serialized_at_any_depth(monkeypatch, key):
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr(settings, "log_transcripts", False)
    event = {"event": "provider_failed", "diagnostic": [{key: {"From": PHONE, "text": SPEECH, "key": SECRET}}]}
    output = json.dumps(privacy_processor(None, "warning", event))
    assert all(s not in output for s in (PHONE, "9876543210", SPEECH, SECRET))


def test_provider_error_is_projected_to_scalar_operational_fields(monkeypatch):
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    event = {"event": "provider_error", "error": {
        "code": "rate_limit_exceeded", "type": "rate_limit_error", "status": 429,
        "message": SPEECH, "detail": {"headers": {"authorization": SECRET}}, "payload": PHONE,
    }, "duration_ms": 123, "attempt": 2, "count": 9876543210}
    result = privacy_processor(None, "warning", event)
    assert result["error"] == {"code": "rate_limit_exceeded", "type": "rate_limit_error", "status": 429}
    assert result["duration_ms"] == 123 and result["attempt"] == 2 and result["count"] == 9876543210
    assert all(s not in json.dumps(result) for s in (SPEECH, SECRET, PHONE))


@pytest.mark.parametrize("value", [
    f"Authorization: Bearer {SECRET}",
    f"password='{SECRET}'", f'api_key="{SECRET}"', f"access_token={SECRET}",
    f"/voice?caller={PHONE}&token={SECRET}",
])
def test_plain_exception_and_request_diagnostics_are_redacted(value):
    result = redact_text(value)
    assert SECRET not in result and "9876543210" not in result


def test_nested_speech_is_withheld_and_cycles_cannot_escape(monkeypatch):
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr(settings, "log_transcripts", False)
    nested = {"transcript": SPEECH, "phone_number": PHONE}
    nested["cycle"] = nested
    event = {"event": "operational", "diagnostic": nested}
    output = json.dumps(privacy_processor(None, "info", event))
    assert SPEECH not in output and "9876543210" not in output and "depth limit" in output


def test_exception_renderer_never_formats_provider_exception_messages(monkeypatch):
    from apps.api.logging import setup_logging

    output = io.StringIO()
    root = logging.getLogger()
    saved_handlers, saved_level, saved_config = root.handlers, root.level, structlog.get_config()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    try:
        setup_logging()
        try:
            raise RuntimeError(f"{SECRET} {SPEECH}")
        except RuntimeError:
            logging.getLogger("provider.test").exception("provider_failed")
        value = output.getvalue()
        assert SECRET not in value and SPEECH not in value
        assert "RuntimeError" in value and "test_pilot_8e_privacy.py" in value
    finally:
        root.handlers, root.level = saved_handlers, saved_level
        structlog.configure(**saved_config)
