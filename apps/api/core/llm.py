"""OpenAI chat-completions wrapper — the single place provider details live.

The agent layer is kept SDK-agnostic by converting the OpenAI response into
local dataclasses (``ChatResult`` and ``ToolCall``). Swapping providers later
means rewriting this module only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from apps.api.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class ToolCall:
    """A single tool invocation requested by the model.

    ``arguments_json`` is left as a raw JSON string — the agent layer parses
    it so this module stays free of tool-specific schema concerns.
    """

    id: str
    name: str
    arguments_json: str


@dataclass
class ChatResult:
    """Normalized chat-completion response surfaced to the agent layer."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = "stop"


# Lazy-instantiated and reused for connection pooling. We defer construction
# because openai>=2.x raises at __init__ when no key is set, which would
# break tests and module imports in environments without an API key. The
# first real request triggers a single construction.
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


# Retry only on transient transport/rate errors. Tool-result re-runs are the
# agent loop's responsibility, not retry — never retry on 4xx other than 429.
_RETRYABLE_TYPES = (RateLimitError, APIConnectionError)


def _should_retry(exc: BaseException) -> bool:
    """Retry on rate-limit, connection errors, and 5xx APIStatusError."""
    if isinstance(exc, _RETRYABLE_TYPES):
        return True
    return isinstance(exc, APIStatusError) and 500 <= exc.status_code < 600


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_should_retry),
)
async def _create_completion(**kwargs: Any) -> Any:
    """Single retried call into the OpenAI SDK."""
    return await _get_client().chat.completions.create(**kwargs)


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.4,
) -> ChatResult:
    """Run one chat-completion turn and return a provider-agnostic result.

    Args:
        messages: OpenAI-formatted messages array (system/user/assistant/tool).
        tools: Optional tool schemas. When provided, ``tool_choice="auto"``
            is forwarded so the model decides whether to call a tool.
        model: Override for ``settings.openai_chat_model``.
        temperature: Sampling temperature; defaults to 0.4 for sales/support
            tone consistency.

    Returns:
        A ``ChatResult`` with text (if any), parallel tool calls (if any),
        token usage, and the OpenAI finish reason.
    """
    effective_model = model or settings.openai_chat_model

    request: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        request["tools"] = tools
        request["tool_choice"] = "auto"

    started = time.monotonic()
    response = await _create_completion(**request)
    duration_ms = int((time.monotonic() - started) * 1000)

    choice = response.choices[0]
    message = choice.message

    tool_calls: list[ToolCall] = []
    raw_tool_calls = getattr(message, "tool_calls", None) or []
    for tc in raw_tool_calls:
        # The SDK uses ``tc.function.name`` / ``tc.function.arguments`` for
        # function-type tool calls. Other types are ignored for now.
        function = getattr(tc, "function", None)
        if function is None:
            continue
        tool_calls.append(
            ToolCall(
                id=tc.id,
                name=function.name,
                arguments_json=function.arguments or "{}",
            )
        )

    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
    finish_reason: str = choice.finish_reason or "stop"

    logger.info(
        "llm_completion",
        model=effective_model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        finish_reason=finish_reason,
        tool_call_count=len(tool_calls),
        duration_ms=duration_ms,
    )

    return ChatResult(
        content=message.content,
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        finish_reason=finish_reason,
    )
