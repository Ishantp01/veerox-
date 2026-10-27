"""VOICE_ASYNC_TOOLS and send_whatsapp_details: a slow tool must not freeze the Realtime event loop, and the agent
may only claim a WhatsApp send that the webhook confirmed."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from apps.api.channels.voice import adapter
from apps.api.config import settings
from apps.api.core import whatsapp_trigger
from apps.api.tests.test_voice_interruption import Call, base  # noqa: F401 - base is an autouse fixture


def _outputs(call: Call) -> list[dict[str, Any]]:
    return [m for m in call.agent.sent if m.get("item", {}).get("type") == "function_call_output"]


@pytest.fixture
def slow_tool(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    release = asyncio.Event()

    async def fake_dispatch(state: Any, name: str, args_json: str, log: Any) -> dict[str, Any]:
        await release.wait()
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_dispatch_deduped", fake_dispatch)
    return release


async def test_sync_mode_blocks_until_the_tool_returns(monkeypatch: pytest.MonkeyPatch, slow_tool: asyncio.Event) -> None:
    monkeypatch.setattr(settings, "voice_async_tools", False)
    call = Call()
    task = asyncio.create_task(call.event("response.function_call_arguments.done", call_id="c1", name="x", arguments="{}"))
    await asyncio.sleep(0.05)
    assert not task.done()  # the event loop is held by the tool
    slow_tool.set()
    await task
    assert len(_outputs(call)) == 1


async def test_async_mode_returns_at_once_and_continues_when_the_tool_finishes(
    monkeypatch: pytest.MonkeyPatch, slow_tool: asyncio.Event
) -> None:
    monkeypatch.setattr(settings, "voice_async_tools", True)
    call = Call()
    await asyncio.wait_for(
        call.event("response.function_call_arguments.done", call_id="c1", name="x", arguments="{}"), 0.5
    )
    assert call.state.tools_in_flight == 1 and not _outputs(call)
    await call.event("input_audio_buffer.speech_started")  # caller events are still handled meanwhile
    assert call.state.caller_speaking
    slow_tool.set()
    await asyncio.sleep(0.1)
    assert len(_outputs(call)) == 1
    assert call.state.tools_in_flight == 0
    assert call.replies()  # the function-calling response was already done, so the continuation is fired here


async def test_async_continuation_waits_for_response_done_when_the_response_is_still_open(
    monkeypatch: pytest.MonkeyPatch, slow_tool: asyncio.Event
) -> None:
    monkeypatch.setattr(settings, "voice_async_tools", True)
    call = Call()
    await call.event("response.created", response={"id": "r1"})
    await call.event("response.function_call_arguments.done", call_id="c1", name="x", arguments="{}")
    slow_tool.set()
    await asyncio.sleep(0.1)
    assert call.state.tool_response_pending and not call.replies()
    await call.event("response.done", response={"id": "r1"})
    assert call.replies()


async def test_a_duplicate_call_id_runs_once(monkeypatch: pytest.MonkeyPatch, slow_tool: asyncio.Event) -> None:
    monkeypatch.setattr(settings, "voice_async_tools", True)
    call = Call()
    for _ in range(2):
        await call.event("response.function_call_arguments.done", call_id="c1", name="x", arguments="{}")
    assert call.state.tools_in_flight == 1
    slow_tool.set()
    await asyncio.sleep(0.1)


# --- send_whatsapp_details (our own WhatsApp service) ---


@pytest.fixture
def wa(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from apps.api.core import tools

    seen: dict[str, Any] = {"text": [], "template": [], "text_result": {"status": "ok"}, "template_result": {"status": "ok"}}

    async def fake_text(db: Any, **kw: Any) -> dict[str, Any]:
        seen["text"].append(kw)
        return seen["text_result"]

    async def fake_template(db: Any, **kw: Any) -> dict[str, Any]:
        seen["template"].append(kw)
        return seen["template_result"]

    monkeypatch.setattr(tools, "send_whatsapp_message", fake_text)
    monkeypatch.setattr(tools, "send_whatsapp_template", fake_template)
    monkeypatch.setattr(settings, "whatsapp_details_demo_link", "Book a demo: https://example.com/demo")
    monkeypatch.setattr(settings, "whatsapp_details_pricing", None)
    monkeypatch.setattr(settings, "whatsapp_details_template", None)
    return seen


async def test_details_sends_the_configured_text_to_the_caller(wa: dict[str, Any]) -> None:
    out = await whatsapp_trigger.send_whatsapp_details(None, "demo_link")  # type: ignore[arg-type]
    assert out["status"] == "ok"
    assert wa["text"][0]["message"].startswith("Book a demo") and wa["text"][0]["phone"] is None


async def test_details_falls_back_to_the_template_outside_the_24h_window(
    wa: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "whatsapp_details_template", "demo_details")
    wa["text_result"] = {"status": "error", "reason": "outside_24h_window"}
    out = await whatsapp_trigger.send_whatsapp_details(None, "demo_link", phone_number="+911")  # type: ignore[arg-type]
    assert out["status"] == "ok"
    assert wa["template"][0]["name"] == "demo_details" and wa["template"][0]["body_params"] == [wa["text"][0]["message"]]


async def test_details_reports_not_sent_when_the_window_is_closed_and_no_template(wa: dict[str, Any]) -> None:
    wa["text_result"] = {"status": "error", "reason": "outside_24h_window"}
    out = await whatsapp_trigger.send_whatsapp_details(None, "demo_link")  # type: ignore[arg-type]
    assert out["status"] == "error" and "Not sent" in out["note"] and not wa["template"]


async def test_details_rejects_unknown_or_unconfigured_interest(wa: dict[str, Any]) -> None:
    assert (await whatsapp_trigger.send_whatsapp_details(None, "memes"))["reason"] == "unknown_interest_type"  # type: ignore[arg-type]
    assert (await whatsapp_trigger.send_whatsapp_details(None, "pricing"))["reason"] == "nothing_configured_to_send"  # type: ignore[arg-type]
    assert not wa["text"]


def test_tool_is_offered_only_when_something_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("demo_link", "pricing", "features"):
        monkeypatch.setattr(settings, f"whatsapp_details_{name}", None)
    assert "send_whatsapp_details" not in [t["name"] for t in adapter.realtime_tools()]
    monkeypatch.setattr(settings, "whatsapp_details_pricing", "Plans start at ...")
    assert "send_whatsapp_details" in [t["name"] for t in adapter.realtime_tools()]
