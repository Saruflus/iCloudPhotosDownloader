"""Dedicated scheduler process entrypoint (D10).

Run as its own container/service so cron triggers fire exactly once:
    python -m app.scheduler_main

Loads enabled schedules into APScheduler, fires download jobs on their cron, and
listens on the Redis `schedules:reload` channel so API edits take effect live.
"""
from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("scheduler")


def _build_service():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from sqlalchemy.orm import Session

    from app.core.database import sync_engine
    from app.services.icloud import get_icloud_service
    from app.services.scheduler import SchedulerService

    def session_factory() -> Session:
        return Session(sync_engine())

    def enqueue(job_id: int) -> str:
        from app.workers.tasks import run_download_job

        return run_download_job.delay(job_id).id

    # Restore the iCloud session up front so unattended jobs can run (D2).
    get_icloud_service().try_restore()

    from app.services.notify import build_notifier

    def auth_ok() -> bool:
        svc = get_icloud_service()
        if svc.get_status()["authenticated"]:
            return True
        # The session may have expired since startup — one restore attempt
        # before declaring the schedule blocked on 2FA (Lot 4).
        return svc.try_restore()

    scheduler = AsyncIOScheduler()
    return scheduler, SchedulerService(
        scheduler, session_factory, enqueue,
        auth_ok=auth_ok, notifier=build_notifier(),
    )


HEARTBEAT_FILE = "/tmp/scheduler-heartbeat"


def _touch_heartbeat() -> None:
    """Liveness marker for the compose healthcheck (Lot 4)."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as fh:
            fh.write("ok")
    except OSError:
        pass


async def main() -> None:
    from app.core.redis import get_redis

    scheduler, service = _build_service()
    service.load()
    _touch_heartbeat()
    scheduler.add_job(_touch_heartbeat, "interval", seconds=30,
                      id="heartbeat", replace_existing=True)
    scheduler.start()
    LOGGER.info("Scheduler started")

    pubsub = get_redis().pubsub()
    await pubsub.subscribe("schedules:reload")
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                LOGGER.info("Reloading schedules")
                service.reload()
    finally:
        await pubsub.unsubscribe("schedules:reload")


if __name__ == "__main__":
    asyncio.run(main())
