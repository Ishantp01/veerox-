"""Offline regressions for defects found in the two outbound pilot calls."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from unittest.mock import AsyncMock

import pytest
import structlog
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from apps.api.channels.voice import adapter, elevenlabs_client
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.config import settings
from apps.api.tests.test_voice_interruption import Call


class Log:
    def __init__(self):
        self.events = []

    def info(self, event, **fields):
        self.events.append((event, fields))

    warning = info


class Socket:
    def __init__(self, failure=None):
        self.close_code = None
        self.failure = failure
        self.read = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.read += 1
        if self.read <= 2:
            return json.dumps({"type": "test.event"})
        self.close_code = 1000 if self.failure is None else 1006
        if self.failure:
            raise self.failure
        raise StopAsyncIteration


@pytest.mark.parametrize(
    "failure",
    [
        None,
        ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True),
        ConnectionClosedError(None, None),
    ],
)
async def test_realtime_iterator_close_logged_once(monkeypatch, failure):
    call, log, socket = Call(), Log(), Socket(failure)
    monkeypatch.setattr(adapter, "handle_openai_event", AsyncMock())
    await bridge._pump_openai_events(socket, call.phone, call.state, log, time.monotonic())
    assert [name for name, _ in log.events] == ["openai_ws_closed"]
    assert log.events[0][1]["origin"] == "peer"


async def test_handler_tts_failure_keeps_reading_realtime(monkeypatch):
    call, log, socket = Call(), Log(), Socket()
    handler = AsyncMock(side_effect=[elevenlabs_client.TTSConnectionClosed("closed"), None])
    monkeypatch.setattr(adapter, "handle_openai_event", handler)
    await bridge._pump_openai_events(socket, call.phone, call.state, log, time.monotonic())
    assert handler.await_count == 2
    assert log.events[0] == ("voice_event_handler_error", {"error_type": "TTSConnectionClosed"})


async def test_tts_send_has_provider_specific_exception():
    session = elevenlabs_client.ElevenLabsTTSSession()
    session._ws = AsyncMock()
    session._ws.send.side_effect = ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)
    with pytest.raises(elevenlabs_client.TTSConnectionClosed):
        await session.send_text("test")
    assert session.failed


async def test_parallel_reply_tasks_send_once(monkeypatch):
    call = Call(reply_pending=True)
    monkeypatch.setattr(adapter, "_voice_decision_response", AsyncMock(return_value=None))
    await asyncio.gather(*(adapter._fire_pending_reply(call.agent, call.state) for _ in range(3)))
    assert len(call.replies()) == 1


async def test_parallel_tools_wait_for_response_done(monkeypatch):
    call = Call(response_active=True)
    monkeypatch.setattr(adapter, "_dispatch_deduped", AsyncMock(return_value={"status": "ok"}))
    monkeypatch.setattr(adapter, "_voice_decision_response", AsyncMock(return_value=None))
    for n in range(2):
        await call.event(
            "response.function_call_arguments.done",
            call_id=f"tool-{n}",
            name="lookup_customer",
            arguments="{}",
        )
    assert call.replies() == []
    await call.event("response.done", response={"id": "original"})
    assert len(call.replies()) == 1


async def test_barge_in_during_tts_send_drops_failure(monkeypatch):
    call = Call(tts_provider="elevenlabs", response_active=True)
    session = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(_):
        entered.set()
        await release.wait()
        raise elevenlabs_client.TTSConnectionClosed("interrupted")

    session.send_text.side_effect = send
    call.state.eleven_session = session
    monkeypatch.setattr(settings, "voice_tts_recovery", False)
    monkeypatch.setattr(settings, "voice_safe_cancel", False)
    monkeypatch.setattr(adapter, "_teardown_elevenlabs_turn", AsyncMock())
    task = asyncio.create_task(call.event("response.text.delta", delta="hello"))
    await entered.wait()
    await adapter._cut_now(call.agent, call.phone, call.state, call.log, "live_fallback")
    release.set()
    await task
    assert call.state.suppress_response_audio
    assert call.cleared()


def test_real_logging_redacts_uvicorn_and_exception(monkeypatch):
    from apps.api.logging import setup_logging

    names = ("", "uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        n: (
            list(logging.getLogger(n).handlers),
            logging.getLogger(n).propagate,
            logging.getLogger(n).disabled,
            logging.getLogger(n).level,
        )
        for n in names
    }
    config, stream = structlog.get_config(), io.StringIO()
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "log_privacy_enabled", True)
    monkeypatch.setattr("sys.stdout", stream)
    try:
        setup_logging()
        logging.getLogger("uvicorn.error").warning("WebSocket /voice?caller=+919876543210")
        try:
            raise ValueError("caller +919876543210")
        except ValueError:
            logging.getLogger("pilot.test").exception("failed")
        logging.getLogger("uvicorn.error").warning("WebSocket /voice?caller=%2B919876543210")
        output = stream.getvalue()
        assert "9876543210" not in output
        assert "WebSocket" in output and "ValueError" in output
        assert logging.getLogger("uvicorn.access").disabled
    finally:
        structlog.configure(**config)
        for name, (handlers, propagate, disabled, level) in saved.items():
            logger = logging.getLogger(name)
            logger.handlers, logger.propagate, logger.disabled, logger.level = (
                handlers,
                propagate,
                disabled,
                level,
            )


async def test_media_during_realtime_outage_does_not_end_call():
    socket = AsyncMock()
    socket.send.side_effect = ConnectionClosedError(None, None)
    for _ in range(3):
        assert not await bridge._append_realtime_audio(socket, "fake-audio")
    socket.send.side_effect = None
    assert await bridge._append_realtime_audio(socket, "fake-audio")


async def test_delayed_tts_open_is_closed_after_barge_in(monkeypatch):
    call = Call(tts_provider="elevenlabs", response_active=True)
    session = AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def acquire(*_):
        entered.set()
        await release.wait()
        return session

    monkeypatch.setattr(adapter, "_acquire_tts_session", acquire)
    monkeypatch.setattr(adapter, "_teardown_elevenlabs_turn", AsyncMock())
    monkeypatch.setattr(settings, "voice_safe_cancel", False)
    task = asyncio.create_task(call.event("response.text.delta", delta="hello"))
    await entered.wait()
    await adapter._cut_now(call.agent, call.phone, call.state, call.log, "live_fallback")
    release.set()
    await task
    session.__aexit__.assert_awaited_once()
    session.send_text.assert_not_awaited()
    assert call.state.eleven_session is None


async def test_actions_remain_deduplicated_across_three_reconnects(monkeypatch):
    call = Call()
    dispatch = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(settings, "voice_reconnect_recovery", True)
    monkeypatch.setattr(adapter, "_dispatch_realtime_tool", dispatch)
    for _ in range(3):
        await adapter._dispatch_deduped(call.state, "book_appointment", "{}", call.log)
        adapter.reset_after_reconnect(call.state)
    assert dispatch.await_count == 1
