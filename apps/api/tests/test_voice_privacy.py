"""Phase 5: log hygiene (core/privacy.py). Phone numbers are masked and obviously sensitive
values redacted in LOG LINES; nothing stored or spoken changes. Pattern-based, so these tests
also state what it deliberately leaves alone.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from apps.api.config import settings
from apps.api.core.privacy import mask_phone, privacy_processor, redact_text


# --- masking ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("+919876543210", "+********3210"),
        ("9876543210", "******3210"),
        ("98765 43210", "******3210"),
        (919876543210, "********3210"),
        ("1234", "***"),
        ("", "***"),
    ],
)
def test_phone_numbers_keep_only_their_last_four_digits(value: Any, expected: str) -> None:
    assert mask_phone(value) == expected


# --- redaction -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "gone", "kept"),
    [
        ("mera otp 482913 hai", "482913", "otp"),
        ("OTP is 4829, please check", "4829", "OTP is"),
        ("your pin: 1234", "1234", "pin"),
        ("cvv 123 hai", "123", "cvv"),
        ("call 9876543210 now", "9876543210", "call"),
        ("call +91 98765 43210 now", "98765 43210", "call"),
        ("Aadhaar 1234 5678 9012 hai", "1234 5678 9012", "Aadhaar"),
        ("card 4111 1111 1111 1111", "4111 1111 1111", "card"),
        ("mail asha.k@example.co.in please", "asha.k@example.co.in", "please"),
        ("PAN ABCDE1234F hai", "ABCDE1234F", "PAN"),
    ],
)
def test_sensitive_values_are_removed_and_the_sentence_stays_readable(text: str, gone: str, kept: str) -> None:
    redacted = redact_text(text)
    assert gone not in redacted and kept in redacted


@pytest.mark.parametrize(
    "text",
    [
        "Rs. 1,50,000 ka investment",
        "price 1500 rupaye hai",
        "appointment 2026-10-20 at 17:00",
        "meeting 2:30 baje",
        "dukaan 800 sq ft ki hai",
        "20% margin",
        "Dava India franchise",
        "",
    ],
)
def test_ordinary_business_numbers_and_words_are_left_alone(text: str) -> None:
    assert redact_text(text) == text


# --- the log processor -----------------------------------------------------------------------------------------------------


@pytest.fixture
def logs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A standalone structlog pipeline (the app's global configuration is not touched)."""
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr(settings, "log_transcripts", True)
    captured: list[dict[str, Any]] = []

    def sink(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event_dict))
        raise structlog.DropEvent

    return captured, structlog.wrap_logger(  # type: ignore[return-value]
        None, processors=[privacy_processor, sink]
    )


def test_a_logged_caller_number_is_masked(logs: Any) -> None:
    captured, log = logs
    log.info("voice_stream_connected", caller="+919876543210", call_uuid="abc-123", provider="plivo")
    assert captured[0]["caller"] == "+********3210"
    assert captured[0]["call_uuid"] == "abc-123" and captured[0]["provider"] == "plivo"  # ids stay correlatable


@pytest.mark.parametrize("key", ["phone", "to", "from", "to_phone", "phone_number", "recipient", "number"])
def test_every_known_phone_field_is_masked(logs: Any, key: str) -> None:
    captured, log = logs
    log.info("x", **{key: "9876543210"})
    assert captured[0][key] == "******3210"


def test_unknown_callers_and_non_text_values_are_left_alone(logs: Any) -> None:
    captured, log = logs
    log.info("x", caller="unknown", phone=None, to=True, count=9876543210)
    assert captured[0]["caller"] == "unknown" and captured[0]["phone"] is None and captured[0]["to"] is True
    assert captured[0]["count"] == 9876543210  # not a phone field


def test_free_text_fields_are_redacted(logs: Any) -> None:
    captured, log = logs
    log.warning("provider_failed", error="send to 9876543210 failed", reason="otp 4829 rejected", text="hello")
    assert "9876543210" not in captured[0]["error"] and "4829" not in captured[0]["reason"]
    assert captured[0]["text"] == "hello"


def test_transcripts_can_be_kept_out_of_the_logs_entirely(logs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    captured, log = logs
    monkeypatch.setattr(settings, "log_transcripts", False)
    log.info("voice_user_transcript", text="mera otp 4829 hai, aur address Pune")
    log.info("voice_assistant_transcript", text="Namaste ji")
    log.info("voice_live_verdict", text="kitna lagega")
    log.info("some_other_event", text="operational text")
    assert captured[0]["text"] == "<35 chars not logged>"
    assert captured[1]["text"] == "<10 chars not logged>" and captured[2]["text"].endswith("chars not logged>")
    assert captured[3]["text"] == "operational text"  # only caller/agent speech is withheld


def test_by_default_transcripts_are_logged_but_redacted(logs: Any) -> None:
    captured, log = logs
    log.info("voice_user_transcript", text="mera otp 4829 hai")
    assert "4829" not in captured[0]["text"] and "otp" in captured[0]["text"]


def test_with_privacy_off_lines_are_untouched_but_transcript_withholding_still_works(
    logs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured, log = logs
    monkeypatch.setattr(settings, "log_privacy_enabled", False)
    log.info("x", caller="+919876543210", text="otp 4829")
    assert captured[0]["caller"] == "+919876543210" and captured[0]["text"] == "otp 4829"
    monkeypatch.setattr(settings, "log_transcripts", False)
    log.info("voice_user_transcript", text="secret")
    assert captured[1]["text"] == "<6 chars not logged>"


def test_the_processor_never_raises_on_odd_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class Weird:
        def __str__(self) -> str:
            raise RuntimeError("no")

    event = {"event": "x", "caller": Weird(), "text": Weird(), "error": 5, "message": None}
    assert privacy_processor(None, "info", event) is event


def test_logging_setup_installs_the_processor() -> None:
    import logging

    from apps.api.logging import setup_logging

    saved_config = structlog.get_config()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    try:
        setup_logging()
        assert privacy_processor in structlog.get_config()["processors"]
    finally:
        structlog.configure(**saved_config)
        root.handlers, root.level = saved_handlers, saved_level
