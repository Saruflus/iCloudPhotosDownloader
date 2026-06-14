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
from datetime import datetime, timezone
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
    date_from: datetime | None = None
    date_to: datetime | None = None
    job_type: str = "download"  # 'download' | 'verify' (Lot 4)


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
        notifier=None,  # Lot 4: optional Notifier (see services/notify.py)
        notify_on_success: bool = False,
        notify_on_failure: bool = True,
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
        self.notifier = notifier
        self.notify_on_success = notify_on_success
        self.notify_on_failure = notify_on_failure

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

        if job.job_type == "verify":
            # Verify/repair (Lot 4): completed assets whose files vanished from
            # disk are reset to pending, then the normal flow below re-downloads
            # everything in scope that isn't completed — including them.
            intact, broken = self._verify_scan(job)
            self._log(job.id, "info",
                      f"Verify: {intact} intact, {broken} with missing files re-queued")

        spec = JobSpec(
            template=job.folder_structure or [],
            download_version=job.download_version,
            album_fanout=job.album_fanout,
            force_redownload=job.force_redownload,
        )
        order, photos, membership = self._collect(job)
        if order is None:  # cancelled during the (possibly long) collect phase
            self.job_store.set_status(job.id, "cancelled")
            self.publisher.publish(job.id, {"type": "done", "status": "cancelled"})
            return "cancelled"
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
        self._notify_result(job.id, status, counts)
        return status

    def _notify_result(self, job_id: int, status: str, counts: Counter) -> None:
        if self.notifier is None:
            return
        try:
            from app.services.notify import notify_job_result

            notify_job_result(
                self.notifier, job_id, status,
                {
                    "downloaded": counts[Outcome.downloaded],
                    "skipped": counts[Outcome.skipped],
                    "failed": counts[Outcome.failed],
                },
                on_success=self.notify_on_success,
                on_failure=self.notify_on_failure,
            )
        except Exception as exc:  # notifications must never fail a job
            LOGGER.warning("Job notification failed: %s", exc)

    def _verify_scan(self, job: JobRecord) -> tuple[int, int]:
        """Check every completed asset's files on disk (Lot 4).

        Missing → status reset to pending (the download phase re-fetches those
        inside the job's album scope); intact → last_verified_at stamped.
        """
        dl, close = self.downloader_factory()
        try:
            store = dl.store
            intact_ids: list[str] = []
            broken_ids: list[str] = []
            for asset_id, files in store.iter_completed():
                paths = [f.get("path") for f in (files or [])]
                if any(p and not os.path.exists(p) for p in paths) or not paths:
                    broken_ids.append(asset_id)
                else:
                    intact_ids.append(asset_id)
            if broken_ids:
                store.reset_to_pending(broken_ids)
            if intact_ids:
                store.touch_verified(intact_ids)
            return len(intact_ids), len(broken_ids)
        finally:
            close()

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

    # how often (in listed assets) the collect loop re-checks for cancellation
    _COLLECT_CANCEL_EVERY = 200

    def _collect(self, job: JobRecord):
        """Walk selected albums; build asset order, photo map, and per-asset
        album membership (for fanout). Apply media-type + asset-id filters.

        Returns (None, None, None) if the job was cancelled mid-walk — listing
        10-20k assets can take minutes, well before any download starts.
        """
        wanted = set(job.selected_asset_ids or [])
        order: list[str] = []
        photos: dict[str, object] = {}
        membership: dict[str, list[str]] = {}
        seen = 0
        for album in job.selected_albums or []:
            for photo in self.icloud.iter_album(album):
                seen += 1
                if seen % self._COLLECT_CANCEL_EVERY == 0 and self.job_store.is_cancel_requested(job.id):
                    self._log(job.id, "info", "Cancelled while listing albums")
                    return None, None, None
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


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _passes_date_range(photo, job: JobRecord) -> bool:
    """Capture-date filter (Lot 2). No range set → everything passes; with a
    range set, undated assets are excluded (a range expresses intent by date)."""
    lo = _ensure_utc(job.date_from)
    hi = _ensure_utc(job.date_to)
    if lo is None and hi is None:
        return True
    created = _ensure_utc(getattr(photo, "created", None))
    if created is None:
        return False
    if lo is not None and created < lo:
        return False
    if hi is not None and created > hi:
        return False
    return True


def _passes_filter(photo, job: JobRecord) -> bool:
    if not _passes_date_range(photo, job):
        return False
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

    from app.core.overrides import load_overrides_sync

    settings = get_settings()
    redis = get_sync_redis()
    icloud = get_icloud_service()
    icloud.try_restore()  # passwordless session restore (D2)
    publisher = RedisProgressPublisher(redis)

    # Settings-page overrides apply per job start — no container restart needed.
    with Session(sync_engine()) as s:
        try:
            overrides = load_overrides_sync(s)
        except Exception:  # table may not exist yet (pre-migration)
            overrides = {}
    concurrency = overrides.get("download_concurrency", settings.download_concurrency)
    max_retries = overrides.get("max_retries", settings.max_retries)
    tz_name = overrides.get("local_timezone", settings.local_timezone)

    def factory():
        session = Session(sync_engine())
        store = SqlAssetStore(session)
        dl = Downloader(
            icloud, store, settings.download_base_path, publisher, tz_name
        )
        return dl, session.close

    from app.services.notify import build_notifier
    from app.workers.job_store import SqlJobStore  # local import to avoid cycle

    return JobRunner(
        job_store=SqlJobStore(Session(sync_engine())),
        lock=LeaseLock(redis),
        downloader_factory=factory,
        icloud=icloud,
        publisher=publisher,
        concurrency=concurrency,
        max_retries=max_retries,
        notifier=build_notifier(),
        notify_on_success=settings.notify_on_success,
        notify_on_failure=settings.notify_on_failure,
    )
