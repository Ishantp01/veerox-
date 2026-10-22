"""Phase 8D follow-up: the one measurement the parallel investigation could not make from the pilot logs.

How many characters had ElevenLabs been given when its first audio arrived? A count only, never the text.
"""

from __future__ import annotations

import uuid
from typing import Any

from apps.api.channels.voice import adapter
from apps.api.tests.test_voice_interruption import _Phone


class _Session:
    chars_sent = 57
    final_seen = True
    fail_reason = None

    async def audio_chunks(self) -> Any:
        for chunk in ("AAAA", "BBBB", "CCCC"):
            yield chunk


def _state() -> adapter.CallState:
    return adapter.CallState(
        user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), org_id=uuid.uuid4(), base_instructions="B"
    )


async def test_the_characters_sent_at_first_audio_are_noted_once_per_turn() -> None:
    state, phone, noted = _state(), _Phone(), []
    state.latency.note = lambda **facts: noted.append(facts)  # type: ignore[method-assign]
    state.eleven_session = _Session()  # type: ignore[assignment]
    await adapter._forward_elevenlabs_audio(phone, state, object())  # type: ignore[arg-type]
    assert noted == [{"tts_chars_at_first_chunk": 57}]  # the first chunk only, not all three
    assert len(phone.sent) == 3  # and the audio itself is still forwarded in full


async def test_a_turn_that_is_cut_off_before_any_audio_notes_nothing() -> None:
    state, phone, noted = _state(), _Phone(), []
    state.latency.note = lambda **facts: noted.append(facts)  # type: ignore[method-assign]
    session = _Session()
    state.eleven_session = session  # type: ignore[assignment]

    async def cut_first(self: Any) -> Any:
        state.generation += 1  # the caller cut in: this turn may no longer play
        yield "AAAA"

    session.audio_chunks = cut_first.__get__(session)  # type: ignore[method-assign]
    await adapter._forward_elevenlabs_audio(phone, state, object())  # type: ignore[arg-type]
    assert noted == [] and phone.sent == []


# --- account ids in HTTP client request lines ---------------------------------------------------------------------------------------------------------

from apps.api.core.privacy import redact_text


def test_provider_account_ids_are_withheld_from_request_urls() -> None:
    plivo = redact_text('HTTP Request: POST https://api.plivo.com/v1/Account/MASYNTHETICACCOUNTID123/Call/ "HTTP/1.1 201 Created"')
    twilio = redact_text("GET https://api.twilio.com/2010-04-01/Accounts/ACSYNTHETIC0000000000000000000000/Calls.json?To=%2B919123456789")
    assert "MASYNTHETICACCOUNTID123" not in plivo and "/Account/[account]/Call/" in plivo
    assert "ACSYNTHETIC0000000000000000000000" not in twilio and "9123456789" not in twilio
    assert "Calls.json" in twilio  # the operation stays visible for debugging


def test_ordinary_paths_are_left_alone() -> None:
    for text in ("/accounts/settings", "/Account/short", "POST /voice/answer", "/v1/Account/"):
        assert redact_text(text) == text
