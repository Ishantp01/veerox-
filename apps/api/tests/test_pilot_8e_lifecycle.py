"""Response identity, duplicate delivery and watchdog regression probes."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock

import pytest

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.tests.test_voice_interruption import Call


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(settings, "conversation_voice_live", False)
    monkeypatch.setattr(settings, "voice_elevenlabs_prewarm", False)


async def test_duplicate_tool_delivery_during_dispatch_runs_once(monkeypatch):
    call = Call()
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"status": "ok"}

    execute = AsyncMock(side_effect=dispatch)
    monkeypatch.setattr(adapter, "_dispatch_deduped", execute)
    await call.event("response.created", response={"id": "tool-response"})
    first = asyncio.create_task(call.event("response.function_call_arguments.done",
        name="lookup_customer", call_id="same-tool", response_id="tool-response", arguments="{}"))
    await entered.wait()
    await call.event("response.function_call_arguments.done", name="lookup_customer",
                     call_id="same-tool", response_id="tool-response", arguments="{}")
    release.set()
    await first
    assert execute.await_count == 1 and call.replies() == []
    await call.event("response.done", response={"id": "tool-response"})
    assert len(call.replies()) == 1


async def test_duplicate_response_done_does_not_release_new_response(monkeypatch):
    call = Call()
    monkeypatch.setattr(adapter, "_dispatch_deduped", AsyncMock(return_value={"status": "ok"}))
    await call.event("response.created", response={"id": "original"})
    await call.event("response.function_call_arguments.done", name="lookup_customer", call_id="one", arguments="{}")
    await call.event("response.done", response={"id": "original"})
    assert call.state.response_active and len(call.replies()) == 1
    await call.event("response.done", response={"id": "original"})
    assert call.state.response_active and call.state.responses_done == 1
    assert len(call.replies()) == 1
    await call.event("response.created", response={"id": "continuation"})
    call.state.resp_text = "already generated"
    await call.event("response.created", response={"id": "continuation"})
    assert call.state.resp_text == "already generated"


async def test_stale_completion_does_not_end_current_response():
    call = Call()
    await call.event("response.created", response={"id": "current"})
    await call.event("response.done", response={"id": "old"})
    assert call.state.response_active and call.state.responses_done == 0


async def test_hard_cancel_timeout_retires_response_before_replacement(monkeypatch):
    call = Call(response_active=True, response_id="timed-out", ack_stage="cancelling",
                cancel_sent_at=time.monotonic(), reply_pending=True)
    schedule = Mock()
    monkeypatch.setattr(adapter.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(adapter, "_schedule_reply", schedule)
    await adapter._cancel_watchdog(call.agent, call.state, call.log)
    assert call.state.cancel_disabled and schedule.call_count == 1
    call.state.response_active = True  # slot reserved, response.created still pending
    await call.event("response.done", response={"id": "timed-out"})
    assert call.state.response_active and call.state.responses_done == 0


async def test_reconnect_discards_only_session_delivery_ids():
    call = Call()
    call.state.seen_tool_call_ids.add("old-tool")
    call.state.completed_response_ids.add("old-response")
    call.state.action_results["business-action"] = {"status": "ok"}
    adapter.reset_after_reconnect(call.state)
    assert call.state.seen_tool_call_ids == set() and call.state.completed_response_ids == set()
    assert call.state.action_results["business-action"] == {"status": "ok"}
