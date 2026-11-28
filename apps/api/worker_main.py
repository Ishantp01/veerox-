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


async def run_until_stopped(tasks: tuple[asyncio.Task[None], ...], stop: asyncio.Event) -> None:
    """Keep the process alive until ``stop`` is set (SIGTERM/SIGINT), then cancel every task.

    A task that finishes early is NOT a reason to exit: some are one-shot (the Plivo number registration returns
    right after startup), and a worker that crashes is logged and left to Sentry rather than restarting the whole
    container, which would re-run every startup requeue pass.
    """
    log = structlog.get_logger("worker")
    stop_wait = asyncio.create_task(stop.wait())
    running = set(tasks)
    try:
        while not stop.is_set() and running:
            done, _ = await asyncio.wait({stop_wait, *running}, return_when=asyncio.FIRST_COMPLETED)
            for task in done - {stop_wait}:
                running.discard(task)
                if not task.cancelled() and task.exception() is not None:
                    log.error("worker_task_crashed", error=repr(task.exception()))
        if not running and not stop.is_set():
            # Every task ended on its own: nothing left to do, so let the container restart cleanly.
            log.warning("worker_all_tasks_finished")
    finally:
        stop_wait.cancel()
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


async def main() -> None:
    setup_logging()
    init_sentry()
    log = structlog.get_logger("worker")
    tasks = start_background_workers()
    if not tasks:
        log.warning("worker_nothing_to_run")
        # Idle instead of exiting, so `restart: unless-stopped` does not loop a deliberately-empty worker.
        await asyncio.Event().wait()
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev
            pass
    log.info("worker_started", tasks=len(tasks))
    await run_until_stopped(tasks, stop)
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
