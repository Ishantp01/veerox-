"""How many live calls this process is carrying, and the limit it enforces.

Until now nothing bounded the number of simultaneous media streams: ``MAX_CONCURRENT_CALLS`` only paces the campaign dialer.
Every stream holds an OpenAI Realtime session, an ElevenLabs socket per turn and database sessions, on one process, so an
unbounded count is a way to exhaust memory or provider rate limits (by accident or by anyone able to open streams).

``VOICE_MAX_ACTIVE_STREAMS`` (0 = no limit, exactly as before) caps the streams admitted here, and also makes a new outbound
dial fail fast when the process is already full, instead of ringing a customer whose call it cannot carry. The count is
process-local by nature (see the horizontal-scaling inventory test); with several instances each enforces its own cap.
"""

from __future__ import annotations

import time

from apps.api.config import settings


class CapacityReached(RuntimeError):
    """This process is carrying as many live calls as ``VOICE_MAX_ACTIVE_STREAMS`` allows."""


_active: dict[str, float] = {}


def limit() -> int:
    try:
        return max(0, int(settings.voice_max_active_streams or 0))
    except (TypeError, ValueError):
        return 0


def active_count() -> int:
    return len(_active)


def has_room() -> bool:
    cap = limit()
    return cap == 0 or len(_active) < cap


def admit(key: str) -> bool:
    """Take a slot for ``key`` (a call id). False when the process is full. Re-admitting a key that already holds a slot
    does not take a second one."""
    if key in _active:
        return True
    if not has_room():
        return False
    _active[key] = time.monotonic()
    return True


def release(key: str) -> None:
    _active.pop(key, None)


def snapshot() -> dict[str, int | None]:
    """Counts only: no call ids, numbers or organisations."""
    now = time.monotonic()
    oldest = max((now - started for started in _active.values()), default=0.0)
    cap = limit()
    return {"active_streams": len(_active), "max_active_streams": cap or None, "oldest_stream_seconds": round(oldest)}
