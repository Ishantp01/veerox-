"""Entry point of the dedicated worker container: ``python -m apps.api.worker_main``.

Runs the background workers (campaign dialer, WhatsApp/follow-up dispatchers, billing/usage jobs) in their own
process so they never share an event loop with the API and live voice calls. Run exactly ONE of these.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from apps.api.logging import setup_logging
from apps.api.sentry import init_sentry
from apps.api.workers.runner import start_background_workers


async def main() -> None:
    setup_logging()
    init_sentry()
    log = structlog.get_logger("worker")
    tasks = start_background_workers()
    if not tasks:
        log.warning("worker_nothing_to_run")
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev
            pass
    log.info("worker_started", tasks=len(tasks))
    stop_wait = asyncio.create_task(stop.wait())
    await asyncio.wait({stop_wait, *tasks}, return_when=asyncio.FIRST_COMPLETED)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
