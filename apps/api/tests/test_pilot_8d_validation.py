"""Call-control lifecycle probes following full-suite validation findings."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from apps.api.channels.voice import call_control
from apps.api.config import settings
from apps.api.tests.test_voice_call_control import Call


async def test_goodbye_continuation_waits_for_tool_response_and_then_hangs_up(monkeypatch):
    hangup, record = AsyncMock(return_value="ok"), AsyncMock()
    monkeypatch.setattr(settings, "voice_end_call_enabled", True)
    monkeypatch.setattr(settings, "voice_end_call_grace_seconds", 0.0)
    monkeypatch.setattr(settings, "voice_end_call_max_wait_seconds", 2.0)
    monkeypatch.setattr(call_control, "provider_hangup", hangup)
    monkeypatch.setattr(call_control, "record_call_outcome", record)
    call = Call(response_active=True)
    await call.event(
        "response.function_call_arguments.done",
        name="end_call",
        call_id="end-1",
        arguments='{"reason":"task_complete"}',
    )
    assert "response.create" not in call.types()
    await call.event("response.done", response={"id": "tool-response"})
    assert call.types().count("response.create") == 1
    assert hangup.await_count == 0
    await call.event("response.created", response={"id": "goodbye"})
    await call.event("response.done", response={"id": "goodbye"})
    await asyncio.wait_for(call.state.hangup_task, timeout=3)
    assert hangup.await_count == 1


async def test_disabled_end_call_resumes_only_after_its_response_done(monkeypatch):
    monkeypatch.setattr(settings, "voice_end_call_enabled", False)
    call = Call(response_active=True)
    await call.event(
        "response.function_call_arguments.done", name="end_call", call_id="end-2", arguments="{}"
    )
    assert call.outputs()[0]["reason"] == "end_call_not_enabled"
    assert "response.create" not in call.types()
    await call.event("response.done", response={"id": "tool-response"})
    assert call.types().count("response.create") == 1
    assert call.state.hangup_task is None
