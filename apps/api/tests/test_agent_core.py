"""Tests for apps.api.core.agent — the AgentCore loop end-to-end.

We mock the LLM at the ``chat_completion`` seam (the wrapper, not the SDK)
and patch the Redis pool the kill-switch checks against. The loop's job is
to glue history + LLM + tools + persistence — these tests verify that.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core import agent as agent_module
from apps.api.core.agent import agent_core
from apps.api.core.llm import ChatResult, ToolCall
from apps.api.db.models import Conversation, Lead, Message, Org, User

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000ccc")


class _FakeRedis:
    """Just enough of Redis for the kill-switch check (plus capture_lead's
    SETNX dedupe and transfer_to_human's rpush, needed by the channel
    dispatch test below, which exercises a real tool handler)."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(agent_module, "get_redis_pool", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _pin_default_org(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure settings.default_org_id matches the ORG_ID we seed in tests."""
    from apps.api import config as config_module

    monkeypatch.setattr(config_module.settings, "default_org_id", str(ORG_ID))


async def _seed_org_and_user(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    db.add(User(id=USER_ID, org_id=ORG_ID, phone="+910000000001", name="Test"))
    await db.commit()


def _patch_llm_sequence(
    monkeypatch: pytest.MonkeyPatch, results: list[ChatResult]
) -> list[list[dict[str, Any]]]:
    """Patch chat_completion to return successive results; capture each call's messages."""
    calls: list[list[dict[str, Any]]] = []
    iterator = iter(results)

    async def fake(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.4,
    ) -> ChatResult:
        calls.append([dict(m) for m in messages])
        return next(iterator)

    monkeypatch.setattr(agent_module, "chat_completion", fake)
    return calls


async def test_handle_turn_plain_text_reply_persists_both_rows(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns text → user + assistant rows land in messages."""
    await _seed_org_and_user(db_session)
    _patch_llm_sequence(
        monkeypatch,
        [ChatResult(content="Hi Asha!", tokens_in=12, tokens_out=4)],
    )

    reply = await agent_core.handle_turn(
        db=db_session,
        user_id=USER_ID,
        channel="whatsapp",
        input_text="hi",
    )

    assert reply == "Hi Asha!"

    msgs = (
        await db_session.execute(
            select(Message).where(Message.user_id == USER_ID).order_by(Message.role.asc())
        )
    ).scalars().all()
    assert len(msgs) == 2
    by_role = {m.role: m for m in msgs}
    assert by_role["user"].content == "hi"
    assert by_role["assistant"].content == "Hi Asha!"
    assert by_role["assistant"].tokens_in == 12
    assert by_role["assistant"].tokens_out == 4

    # A conversation row was opened for the (user, whatsapp) pair.
    convs = (await db_session.execute(select(Conversation))).scalars().all()
    assert len(convs) == 1
    assert convs[0].channel == "whatsapp"


async def test_handle_turn_kill_switch_short_circuits(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the kill switch is set, return the canned reply and skip the LLM."""
    await _seed_org_and_user(db_session)
    fake_redis.kv["veerox:kill_switch"] = "1"

    # The LLM should NEVER be called — patch with a sentinel that explodes.
    async def explode(*_args: Any, **_kwargs: Any) -> ChatResult:
        raise AssertionError("chat_completion was called despite kill switch")

    monkeypatch.setattr(agent_module, "chat_completion", explode)

    reply = await agent_core.handle_turn(
        db=db_session,
        user_id=USER_ID,
        channel="whatsapp",
        input_text="any input",
    )

    assert "back in a moment" in reply.lower()
    # No turn was persisted — the user's input doesn't count as a real turn
    # when the agent is paused (matches plan §5.6 intent).
    msgs = (await db_session.execute(select(Message))).scalars().all()
    assert msgs == []


async def test_handle_turn_dispatches_tool_then_replies(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tool call → handler dispatched → result fed back → final assistant text persisted."""
    await _seed_org_and_user(db_session)

    # Iteration 1: model asks for lookup_customer. Iteration 2: model replies.
    _patch_llm_sequence(
        monkeypatch,
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="lookup_customer",
                        arguments_json='{"phone":"+910000000001"}',
                    )
                ],
                tokens_in=20,
                tokens_out=5,
                finish_reason="tool_calls",
            ),
            ChatResult(
                content="You're Test, your last contact was just now.",
                tokens_in=30,
                tokens_out=12,
            ),
        ],
    )

    reply = await agent_core.handle_turn(
        db=db_session,
        user_id=USER_ID,
        channel="whatsapp",
        input_text="who am I?",
    )

    assert "Test" in reply

    msgs = (
        await db_session.execute(
            select(Message).where(Message.user_id == USER_ID).order_by(Message.role.asc())
        )
    ).scalars().all()
    # Only user + final assistant land via persist_turn — intermediate
    # assistant/tool messages live inside the agent loop's messages list.
    assert {m.role for m in msgs} == {"user", "assistant"}
    by_role = {m.role: m for m in msgs}
    assert by_role["user"].content == "who am I?"
    # Token totals accumulate across both LLM calls.
    assert by_role["assistant"].tokens_in == 50
    assert by_role["assistant"].tokens_out == 17


async def test_handle_turn_forwards_channel_into_tool_dispatch(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The channel handle_turn was called with reaches the tool handler's Lead row."""
    from apps.api.core import tools as tools_module

    monkeypatch.setattr(tools_module, "get_redis_pool", lambda: fake_redis)

    _patch_llm_sequence(
        monkeypatch,
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="capture_lead",
                        arguments_json='{"phone":"+910000000001","intent":"quote"}',
                    )
                ],
                tokens_in=10,
                tokens_out=2,
                finish_reason="tool_calls",
            ),
            ChatResult(content="Got it, thanks!", tokens_in=5, tokens_out=3),
        ],
    )
    await _seed_org_and_user(db_session)

    reply = await agent_core.handle_turn(
        db=db_session,
        user_id=USER_ID,
        channel="whatsapp",
        input_text="I'd like a quote",
    )

    assert reply == "Got it, thanks!"
    lead = (
        await db_session.execute(select(Lead).where(Lead.intent == "quote"))
    ).scalars().one()
    assert lead.channel == "whatsapp"


async def test_handle_turn_unknown_tool_returns_error_to_model(
    db_session: AsyncSession,
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model invents a tool name → loop returns an error result and continues."""
    await _seed_org_and_user(db_session)

    captured_calls = _patch_llm_sequence(
        monkeypatch,
        [
            ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(id="x", name="this_tool_does_not_exist", arguments_json="{}")
                ],
                finish_reason="tool_calls",
            ),
            ChatResult(content="Sorry, that didn't work.", tokens_in=1, tokens_out=2),
        ],
    )

    reply = await agent_core.handle_turn(
        db=db_session,
        user_id=USER_ID,
        channel="whatsapp",
        input_text="try a bogus tool",
    )

    assert reply == "Sorry, that didn't work."

    # The second LLM call sees a tool message with status=error.
    second_call_msgs = captured_calls[1]
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    parsed = json.loads(tool_msgs[0]["content"])
    assert parsed["status"] == "error"
    assert "unknown_tool" in parsed["reason"]
