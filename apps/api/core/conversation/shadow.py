"""Voice shadow mode — run the engine on a call transcript turn, log only.

Fire-and-forget: scheduled from the voice adapter after a turn is persisted,
never awaited on the audio path, and never able to affect the call. It uses
its own DB session, executes no actions, writes no memory and does not make
the phrasing LLM call.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from apps.api.config import settings
from apps.api.core.conversation import voice_decision
from apps.api.core.conversation.engine import run_engine
from apps.api.core.memory import load_last_n
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.db.models.org import Org
from apps.api.db.session import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# Hard ceiling for one shadow run so a stuck LLM call can't pile up tasks.
SHADOW_TASK_TIMEOUT_SECONDS = 15.0

# Strong references so the event loop doesn't garbage-collect running tasks.
_tasks: set[asyncio.Task[None]] = set()


async def _run_shadow(org_id: UUID, user_id: UUID, conversation_id: UUID, text: str) -> None:
    # Cheap, cached check first: calls for orgs that are not in log-only mode
    # (almost all of them) must not open a database session on every turn.
    if await voice_decision.org_voice_mode(org_id) != "shadow":
        return
    async with AsyncSessionLocal() as db:
        org = await db.get(Org, org_id)
        if org is None or org.conversation_mode not in ("human_conversation", "smart_script"):
            return
        # "live" turns are logged by the live decision itself; "off" logs nothing.
        if org.conversation_voice_mode != "shadow":
            return
        history = await load_last_n(db, user_id, n=6)
        await run_engine(
            db,
            org_id=org_id,
            user_id=user_id,
            conversation_id=conversation_id,
            channel="voice",
            text=text,
            history=history,
            api_key=resolve_openai_api_key(org),
            dispatch=None,
            mode="shadow",
        )


async def _guarded(org_id: UUID, user_id: UUID, conversation_id: UUID, text: str) -> None:
    try:
        await asyncio.wait_for(
            _run_shadow(org_id, user_id, conversation_id, text),
            timeout=SHADOW_TASK_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — shadow must never surface anywhere
        logger.warning("conversation_shadow_failed", exc_info=True)


def schedule_voice_shadow(org_id: UUID, user_id: UUID, conversation_id: UUID, text: str) -> None:
    """Queue a shadow run. No-op when disabled or the transcript is a placeholder."""
    if not settings.conversation_voice_shadow:
        return
    if not text or text.strip() in ("", "(voice)"):
        return
    try:
        task = asyncio.get_running_loop().create_task(
            _guarded(org_id, user_id, conversation_id, text)
        )
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
