"""Script-derived greeting: a new script derives a new greeting, an unchanged script reuses the existing one."""

from __future__ import annotations

import pytest

from apps.api.channels.voice import greeting
from apps.api.core.llm import ChatResult


@pytest.fixture(autouse=True)
def _clean():
    greeting._derived.clear()
    yield
    greeting._derived.clear()


def _fake_llm(calls: list[str], reply: str):
    async def fake(messages, **kwargs):
        calls.append(messages[-1]["content"])
        return ChatResult(content=reply)

    return fake


@pytest.mark.asyncio
async def test_same_script_is_derived_once(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("apps.api.core.llm.chat_completion", _fake_llm(calls, "Namaste, main Veer, AI assistant hoon."))
    assert greeting.peek_script_greeting("script A", "Brand", True) is None
    first = await greeting.derive_script_greeting("script A", "Brand", True)
    second = await greeting.derive_script_greeting("script A", "Brand", True)
    assert first == second == "Namaste, main Veer, AI assistant hoon."
    assert len(calls) == 1
    assert greeting.peek_script_greeting("script A", "Brand", True) == first


@pytest.mark.asyncio
async def test_edited_script_gets_a_new_greeting(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("apps.api.core.llm.chat_completion", _fake_llm(calls, "Hello, I am an AI assistant."))
    await greeting.derive_script_greeting("script A", "Brand", True)
    assert greeting.peek_script_greeting("script A v2", "Brand", True) is None
    await greeting.derive_script_greeting("script A v2", "Brand", True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_unusable_result_is_not_cached(monkeypatch):
    calls: list[str] = []
    # No AI disclosure in the reply while disclosure is required -> rejected, retried next time.
    monkeypatch.setattr("apps.api.core.llm.chat_completion", _fake_llm(calls, "Namaste, kaise hain aap?"))
    assert await greeting.derive_script_greeting("script A", "Brand", True) is None
    assert greeting.peek_script_greeting("script A", "Brand", True) is None


@pytest.mark.asyncio
async def test_llm_failure_returns_none(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.api.core.llm.chat_completion", boom)
    assert await greeting.derive_script_greeting("script A", "Brand", True) is None
