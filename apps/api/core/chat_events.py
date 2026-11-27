"""In-process fan-out of "a chat message was stored" events to open SSE streams.

The admin chat page subscribes through ``GET /admin/chat/events`` and refetches
the moment a message lands, instead of waiting for its next poll. The API runs a
single uvicorn worker, so the webhook that saves a message and the SSE stream
that announces it live in the same process. If that ever changes, the page's
slow backup poll still keeps the chat correct, just not instant.

Events carry ids only (never message text); the page refetches through the
normal, permission-checked chat endpoints.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

_MAX_QUEUE = 100
_subscribers: dict[UUID, set[asyncio.Queue[str]]] = {}


def subscribe(org_id: UUID) -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=_MAX_QUEUE)
    _subscribers.setdefault(org_id, set()).add(queue)
    return queue


def unsubscribe(org_id: UUID, queue: asyncio.Queue[str]) -> None:
    queues = _subscribers.get(org_id)
    if queues is None:
        return
    queues.discard(queue)
    if not queues:
        _subscribers.pop(org_id, None)


def publish(org_id: UUID, user_id: UUID) -> None:
    """Announce a new chat message for ``user_id``. Never raises."""
    for queue in list(_subscribers.get(org_id, ())):
        try:
            queue.put_nowait(str(user_id))
        except asyncio.QueueFull:
            # A stuck client: drop the event; its backup poll catches up.
            pass
