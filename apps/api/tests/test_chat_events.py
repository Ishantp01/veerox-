import asyncio
import uuid

import pytest

from apps.api.core import chat_events


def test_publish_reaches_only_same_org_subscribers() -> None:
    org, other, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mine = chat_events.subscribe(org)
    theirs = chat_events.subscribe(other)
    chat_events.publish(org, user)
    assert mine.get_nowait() == str(user)
    assert theirs.empty()
    chat_events.unsubscribe(org, mine)
    chat_events.unsubscribe(other, theirs)
    chat_events.publish(org, user)  # no subscribers left: must not raise


def test_full_queue_drops_instead_of_raising() -> None:
    org = uuid.uuid4()
    queue = chat_events.subscribe(org)
    for _ in range(500):
        chat_events.publish(org, uuid.uuid4())
    assert queue.qsize() == queue.maxsize
    chat_events.unsubscribe(org, queue)


@pytest.mark.asyncio
async def test_stream_emits_hello_event_and_cleans_up() -> None:
    from apps.api.routers.chat import chat_events_stream

    org, user = uuid.uuid4(), uuid.uuid4()
    response = await chat_events_stream(org)
    gen = response.body_iterator
    assert "connected" in await gen.__anext__()
    chat_events.publish(org, user)
    assert await asyncio.wait_for(gen.__anext__(), 2) == f"event: message\ndata: {user}\n\n"
    await gen.aclose()
    assert org not in chat_events._subscribers
