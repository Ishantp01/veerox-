"""VOICE_LISTEN_FIRST_INBOUND: only calls the webhook marked as inbound skip the opening greeting."""

from __future__ import annotations

import pytest

from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.config import Settings


@pytest.fixture(autouse=True)
def _clean() -> None:
    bridge._inbound_calls.clear()


def test_off_by_default() -> None:
    assert Settings.model_fields["voice_listen_first_inbound"].default is False


def test_marked_call_is_taken_once() -> None:
    bridge.mark_inbound_call("c1")
    assert bridge.take_inbound_call("c1") is True
    assert bridge.take_inbound_call("c1") is False  # consumed


def test_unmarked_or_blank_call_greets_first() -> None:
    assert bridge.take_inbound_call("never-marked") is False
    assert bridge.take_inbound_call("") is False
    bridge.mark_inbound_call("")
    assert bridge._inbound_calls == {}


def test_expired_mark_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge.mark_inbound_call("c2")
    bridge._inbound_calls["c2"] -= bridge._INBOUND_TTL_SECONDS + 1
    assert bridge.take_inbound_call("c2") is False


def test_note_asks_for_ai_disclosure() -> None:
    assert "AI assistant" in bridge.LISTEN_FIRST_NOTE


def test_human_style_block_is_opt_in_and_keeps_the_disclosure_rule() -> None:
    from apps.api.core.prompts import render_voice_append

    plain = render_voice_append("Acme")
    styled = render_voice_append("Acme", human_style=True)
    assert "VOICE_HUMAN_STYLE" not in plain
    assert styled.startswith(plain.rstrip("\n"))
    assert "dot com" in styled and "at the rate" in styled and "AI assistant" in styled
    assert Settings.model_fields["voice_human_style"].default is False
