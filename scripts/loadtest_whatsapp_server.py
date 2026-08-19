"""Runs the real app with OpenAI chat completions and the WhatsApp send
client faked out, so a webhook-throughput load test exercises the real
code path (signature verification, Redis dedup, DB writes, agent loop
plumbing) without spending real OpenAI money or sending real WhatsApp
messages.

    NEVER set this as a production service's start command — same rule as
    scripts/loadtest_server.py. Standalone entry point only, never imported
    by apps/api/main.py or any normal request path.

Usage:
    python scripts/loadtest_whatsapp_server.py
Respects PORT env var; defaults to 8198 locally.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import structlog
import uvicorn

logger = structlog.get_logger(__name__)


async def _fake_chat_completion(messages, tools=None, model=None, temperature=0.4):
    from apps.api.core.llm import ChatResult

    return ChatResult(content="Sure, I can help with that!", tokens_in=20, tokens_out=8)


async def _fake_send_text(phone, text, phone_number_id=None):
    return {"messages": [{"id": "fake-wamid"}]}


async def _fake_send_template(phone, template_name, body_params=None, phone_number_id=None):
    return {"messages": [{"id": "fake-wamid"}]}


async def _fake_mark_read(message_id, typing=False, phone_number_id=None):
    return None


def main() -> None:
    logger.warning(
        "loadtest_whatsapp_server_starting_with_fakes",
        warning=(
            "OpenAI chat completions and WhatsApp sends are FAKED. Must "
            "only ever run on a disposable staging service, never on "
            "production."
        ),
    )

    from apps.api.channels.whatsapp import client as wa_client
    from apps.api.core import agent as agent_module

    agent_module.chat_completion = _fake_chat_completion
    wa_client.send_text = _fake_send_text
    wa_client.send_template = _fake_send_template
    wa_client.mark_read = _fake_mark_read

    from apps.api.main import app  # noqa: E402  (patches must land before this import's routers bind)

    port = int(os.environ.get("PORT", "8198"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
