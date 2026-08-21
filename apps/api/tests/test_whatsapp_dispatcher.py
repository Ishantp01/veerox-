"""Tests for apps.api.workers.whatsapp_dispatcher's pure param-resolution
helper — the piece that maps a campaign's configured {{1}}/{{2}}/... tokens
(contact name from the file, send date/time, or a fixed value) to the
actual body_params sent to Meta at send time.
"""

from __future__ import annotations

from datetime import datetime

from apps.api.workers.whatsapp_dispatcher import _SEND_TIME_ZONE, _resolve_template_body_params


def test_resolve_template_body_params_none_config_returns_empty() -> None:
    assert _resolve_template_body_params(None, "Asha") == []
    assert _resolve_template_body_params([], "Asha") == []


def test_resolve_template_body_params_contact_name_token() -> None:
    resolved = _resolve_template_body_params(["{{contact_name}}"], "Asha Verma")
    assert resolved == ["Asha Verma"]


def test_resolve_template_body_params_contact_name_falls_back_when_missing() -> None:
    resolved = _resolve_template_body_params(["{{contact_name}}"], None)
    assert resolved == ["there"]


def test_resolve_template_body_params_date_and_time_reflect_now_in_ist() -> None:
    before = datetime.now(_SEND_TIME_ZONE)
    resolved = _resolve_template_body_params(["{{send_date}}", "{{send_time}}"], "Asha")
    after = datetime.now(_SEND_TIME_ZONE)

    assert resolved[0] == before.strftime("%d %b %Y") or resolved[0] == after.strftime("%d %b %Y")
    # Time is formatted "%I:%M %p" — sanity-check the shape rather than an
    # exact match, since a minute boundary could tick between before/after.
    assert len(resolved[1]) in (7, 8)
    assert resolved[1].upper().endswith(("AM", "PM"))


def test_resolve_template_body_params_mixed_tokens_and_literal_text() -> None:
    resolved = _resolve_template_body_params(
        ["{{contact_name}}", "Diwali Sale", "{{send_date}}"], "Rohit"
    )
    assert resolved[0] == "Rohit"
    assert resolved[1] == "Diwali Sale"
    assert resolved[2] == datetime.now(_SEND_TIME_ZONE).strftime("%d %b %Y")
