"""Celery task + JobRunner — drives one download job (D3, D4, D13, D16).

`JobRunner` holds all the orchestration and takes injected dependencies (job
store, lease lock, a downloader factory, the iCloud service, a publisher) so it's
unit-testable without a broker, DB, or Redis. The Celery task is a thin wrapper
that wires in the concrete SQLAlchemy/Redis implementations.

Job flow:
  acquire lease lock (heartbeat) → running → collect assets across selected
  albums (building per-asset album membership for fanout) → filter → download
  with bounded concurrency, retry+backoff, cooperative cancel → update counters
  and publish progress → final status → release lock.
"""
from __future__ import annotations

import logging
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from app.core.locks import LeaseLock, LockHeld
from app.core.paths import classify_media
from app.services.downloader import Downloader, JobSpec, Outcome

LOGGER = logging.getLogger(__name__)


@dataclass
class JobRecord:
    id: int
    selected_albums: list[str] = field(default_factory=list)
    selected_asset_ids: list[str] = field(default_factory=list)
    folder_structure: list[str] = field(default_factory=list)
    include_raw: bool = False
    include_jpeg: bool = True
    include_heic: bool = True
    include_video: bool = True
    download_version: str = "edited"
    album_fanout: bool = True
    force_redownload: bool = False


class JobStore(Protocol):
    def get(self, job_id: int) -> JobRecord | None: ...
    def set_status(self, job_id: int, status: str) -> None: ...
    def set_total(self, job_id: int, total: int) -> None: ...
    def update_counts(self, job_id: int, *, downloaded: int, skipped: int, failed: int) -> None: ...
    def is_cancel_requested(self, job_id: int) -> bool: ...


# downloader_factory() -> (Downloader, close_callable)
DownloaderFactory = Callable[[], "tuple[Downloader, Callable[[], None]]"]


class JobRunner:
    def __init__(
        self,
        job_store: JobStore,
        lock: LeaseLock,
        downloader_factory: DownloaderFactory,
        icloud,
        publisher,
        concurrency: int = 4,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.job_store = job_store
        self.lock = lock
        self.downloader_factory = downloader_factory
        self.icloud = icloud
        self.publisher = publisher
        self.concurrency = max(1, concurrency)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.sleep = sleep

    def run(self, job_id: int) -> str:
        job = self.job_store.get(job_id)
        if job is None:
            return "failed"
        try:
            with self.lock.hold(f"job-{job_id}"):
                return self._run_locked(job)
        except LockHeld as exc:
            self._log(job_id, "error", f"Another sync is running ({exc.owner}); aborting.")
            self.job_store.set_status(job_id, "failed")
            return "failed"

    # ------------------------------------------------------------- internals
    def _run_locked(self, job: JobRecord) -> str:
        self.job_store.set_status(job.id, "running")
        self._log(job.id, "info", "Job started")
        spec = JobSpec(
            template=job.folder_structure or [],
            download_version=job.download_version,
            album_fanout=job.album_fanout,
            force_redownload=job.force_redownload,
        )
        order, photos, membership = self._collect(job)
        total = len(order)
        self.job_store.set_total(job.id, total)

        counts: Counter = Counter()
        cancelled = False
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {}
            for aid in order:
                if self.job_store.is_cancel_requested(job.id):
                    cancelled = True
                    break
                fut = pool.submit(self._one, photos[aid], membership[aid], spec, job.id)
                futures[fut] = aid
            for fut in as_completed(futures):
                counts[fut.result()] += 1
                self._report(job.id, counts, total)

        if cancelled:
            status = "cancelled"
        elif total and counts[Outcome.downloaded] == 0 and counts[Outcome.skipped] == 0 and counts[Outcome.failed]:
            status = "failed"
        else:
            status = "completed"
        self.job_store.set_status(job.id, status)
        self.publisher.publish(job.id, {"type": "done", "status": status})
        return status

    def _one(self, photo, albums, spec, job_id) -> Outcome:
        dl, close = self.downloader_factory()
        try:
            attempts = self.max_retries + 1
            out = Outcome.failed
            for i in range(attempts):
                out = dl.download_asset(photo, albums, spec, job_id)
                if out != Outcome.failed:
                    return out
                if i < attempts - 1:
                    self.sleep(self.backoff_base ** i)
            return out
        finally:
            close()

    def _collect(self, job: JobRecord):
        """Walk selected albums; build asset order, photo map, and per-asset
        album membership (for fanout). Apply media-type + asset-id filters."""
        wanted = set(job.selected_asset_ids or [])
        order: list[str] = []
        photos: dict[str, object] = {}
        membership: dict[str, list[str]] = {}
        for album in job.selected_albums or []:
            for photo in self.icloud.iter_album(album):
                aid = photo.id
                if wanted and aid not in wanted:
                    continue
                if not _passes_filter(photo, job):
                    continue
                membership.setdefault(aid, [])
                if album not in membership[aid]:
                    membership[aid].append(album)
                if aid not in photos:
                    photos[aid] = photo
                    order.append(aid)
        return order, photos, membership

    def _report(self, job_id, counts: Counter, total: int) -> None:
        self.job_store.update_counts(
            job_id,
            downloaded=counts[Outcome.downloaded],
            skipped=counts[Outcome.skipped],
            failed=counts[Outcome.failed],
        )
        self.publisher.publish(job_id, {
            "type": "progress",
            "downloaded": counts[Outcome.downloaded],
            "skipped": counts[Outcome.skipped],
            "failed": counts[Outcome.failed],
            "total": total,
        })

    def _log(self, job_id, level, message) -> None:
        self.publisher.publish(job_id, {"type": "log", "level": level, "message": message})


def _category(photo) -> str:
    if getattr(photo, "item_type", None) == "movie":
        return "Video"
    return classify_media(Path(photo.filename).suffix)


def _passes_filter(photo, job: JobRecord) -> bool:
    cat = _category(photo)
    if cat == "Video":
        return job.include_video
    if cat == "RAW":
        return job.include_raw
    if cat == "HEIC":
        return job.include_heic
    if cat == "JPEG":
        return job.include_jpeg
    return True  # PNG / other images: included by default


# ============================================================ Celery wrapper
from celery import Celery  # noqa: E402

celery_app = Celery("icloud_sync")
celery_app.conf.broker_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
celery_app.conf.result_backend = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
celery_app.conf.task_track_started = True


@celery_app.task(name="run_download_job")
def run_download_job(job_id: int) -> str:
    return _build_runner().run(job_id)


def _build_runner() -> JobRunner:
    """Wire concrete SQLAlchemy + Redis implementations (prod path)."""
    from sqlalchemy.orm import Session

    from app.core.config import get_settings
    from app.core.database import sync_engine
    from app.core.redis import get_sync_redis
    from app.services.downloader import RedisProgressPublisher, SqlAssetStore
    from app.services.icloud import get_icloud_service

    settings = get_settings()
    redis = get_sync_redis()
    icloud = get_icloud_service()
    icloud.try_restore()  # passwordless session restore (D2)
    publisher = RedisProgressPublisher(redis)

    def factory():
        session = Session(sync_engine())
        store = SqlAssetStore(session)
        dl = Downloader(
            icloud, store, settings.download_base_path, publisher, settings.local_timezone
        )
        return dl, session.close

    from app.workers.job_store import SqlJobStore  # local import to avoid cycle

    return JobRunner(
        job_store=SqlJobStore(Session(sync_engine())),
        lock=LeaseLock(redis),
        downloader_factory=factory,
        icloud=icloud,
        publisher=publisher,
        concurrency=settings.download_concurrency,
        max_retries=settings.max_retries,
    )
