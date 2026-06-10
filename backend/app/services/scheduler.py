"""Scheduler service (D10).

Cron helpers (pure, tested) + an APScheduler-based service that fires download
jobs from saved schedules. Runs in a dedicated single-instance process
(`scheduler_main.py`) so triggers never double-fire under multi-worker FastAPI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from croniter import croniter

LOGGER = logging.getLogger(__name__)

# Config keys copied into a DownloadJob when a schedule fires.
CONFIG_KEYS = (
    "selected_albums", "selected_asset_ids", "folder_structure",
    "include_raw", "include_jpeg", "include_heic", "include_video",
    "download_version", "album_fanout", "force_redownload",
)


def valid_cron(expr: str) -> bool:
    try:
        return bool(croniter.is_valid(expr))
    except Exception:
        return False


def next_run_after(expr: str, base: datetime | None = None) -> datetime:
    base = base or datetime.now(timezone.utc)
    return croniter(expr, base).get_next(datetime)


def build_job_spec(job_config: dict) -> dict:
    """Project a schedule's job_config onto DownloadJob creation fields."""
    return {k: job_config.get(k) for k in CONFIG_KEYS if job_config.get(k) is not None}


# ============================================================ prod service
class SchedulerService:
    """Wires saved schedules into APScheduler. Exercised at deploy."""

    def __init__(self, scheduler, session_factory, enqueue) -> None:
        self.scheduler = scheduler
        self.session_factory = session_factory  # () -> sync Session
        self.enqueue = enqueue  # (job_id) -> task_id

    def load(self) -> None:
        from app.models.assets import Schedule

        with self.session_factory() as session:
            schedules = session.query(Schedule).filter_by(enabled=True).all()
            for sched in schedules:
                self._register(sched.id, sched.cron_expression)
        LOGGER.info("Scheduler loaded enabled schedules")

    def reload(self) -> None:
        self.scheduler.remove_all_jobs()
        self.load()

    def _register(self, schedule_id: int, cron: str) -> None:
        from apscheduler.triggers.cron import CronTrigger

        self.scheduler.add_job(
            self._fire, CronTrigger.from_crontab(cron),
            args=[schedule_id], id=f"schedule-{schedule_id}", replace_existing=True,
        )

    def _fire(self, schedule_id: int) -> None:
        from app.models.assets import DownloadJob, Schedule

        with self.session_factory() as session:
            sched = session.get(Schedule, schedule_id)
            if sched is None or not sched.enabled:
                return
            spec = build_job_spec(sched.job_config or {})
            job = DownloadJob(status="pending", **spec)
            session.add(job)
            session.commit()
            session.refresh(job)
            task_id = self.enqueue(job.id)
            job.celery_task_id = task_id
            sched.last_run_at = datetime.now(timezone.utc)
            sched.next_run_at = next_run_after(sched.cron_expression)
            session.commit()
        LOGGER.info("Schedule %s fired job %s", schedule_id, job.id)
