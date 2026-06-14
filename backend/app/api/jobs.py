"""Jobs API — create/launch, list, detail, cancel, retry, dry-run preview.

Endpoints only create the DB row and enqueue the Celery task; all download work
happens in the worker (note 3). DB + queue are injected (overridable in tests).

retry-failed simply clones the job config into a new job: because completed
assets are skipped and failed ones are retried (asset-ID-as-truth), re-running
the same scope naturally re-attempts only the failures.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_service
from app.core.database import get_async_session
from app.core.security import require_secret

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_secret)])

CONFIG_KEYS = (
    "selected_albums", "selected_asset_ids", "folder_structure",
    "include_raw", "include_jpeg", "include_heic", "include_video",
    "download_version", "album_fanout", "force_redownload",
    "date_from", "date_to", "job_type",
)


# ------------------------------------------------------------------ schemas
class CreateJobBody(BaseModel):
    selected_albums: list[str] = Field(default_factory=list)
    selected_asset_ids: list[str] = Field(default_factory=list)
    folder_structure: list[str] = Field(default_factory=list)
    include_raw: bool = False
    include_jpeg: bool = True
    include_heic: bool = True
    include_video: bool = True
    download_version: str = "edited"
    album_fanout: bool = True
    force_redownload: bool = False
    date_from: datetime | None = None
    date_to: datetime | None = None
    job_type: str = "download"


class JobOut(BaseModel):
    id: int
    created_at: datetime | None = None
    status: str = "pending"
    selected_albums: list[str] = Field(default_factory=list)
    selected_asset_ids: list[str] = Field(default_factory=list)
    folder_structure: list[str] = Field(default_factory=list)
    include_raw: bool = False
    include_jpeg: bool = True
    include_heic: bool = True
    include_video: bool = True
    download_version: str = "edited"
    album_fanout: bool = True
    force_redownload: bool = False
    date_from: datetime | None = None
    date_to: datetime | None = None
    job_type: str = "download"
    total_assets: int = 0
    downloaded_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    celery_task_id: str | None = None


# --------------------------------------------------------------- queue + repo
class JobQueue(Protocol):
    def enqueue(self, job_id: int) -> str: ...
    def revoke(self, task_id: str) -> None: ...


class CeleryJobQueue:
    def enqueue(self, job_id: int) -> str:
        from app.workers.tasks import run_download_job

        return run_download_job.delay(job_id).id

    def revoke(self, task_id: str) -> None:
        from app.workers.tasks import celery_app

        celery_app.control.revoke(task_id, terminate=True)


def _job_to_dict(j) -> dict:
    return {
        "id": j.id, "created_at": j.created_at, "status": j.status,
        "selected_albums": j.selected_albums or [], "selected_asset_ids": j.selected_asset_ids or [],
        "folder_structure": j.folder_structure or [],
        "include_raw": j.include_raw, "include_jpeg": j.include_jpeg,
        "include_heic": j.include_heic, "include_video": j.include_video,
        "download_version": j.download_version, "album_fanout": j.album_fanout,
        "force_redownload": j.force_redownload,
        "date_from": j.date_from, "date_to": j.date_to,
        "job_type": getattr(j, "job_type", "download") or "download",
        "total_assets": j.total_assets,
        "downloaded_count": j.downloaded_count, "skipped_count": j.skipped_count,
        "failed_count": j.failed_count, "celery_task_id": j.celery_task_id,
    }


class SqlJobsRepository:
    """Async SQLAlchemy repo (D1). Exercised in integration/deploy; endpoints are
    unit-tested with a fake implementing the same async surface."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def create(self, spec: dict) -> dict:
        from app.models.assets import DownloadJob

        job = DownloadJob(status="pending", **{k: spec.get(k) for k in CONFIG_KEYS if spec.get(k) is not None})
        self.s.add(job)
        await self.s.commit()
        await self.s.refresh(job)
        return _job_to_dict(job)

    async def set_task_id(self, job_id: int, task_id: str) -> None:
        from app.models.assets import DownloadJob

        job = await self.s.get(DownloadJob, job_id)
        if job is not None:
            job.celery_task_id = task_id
            await self.s.commit()

    async def list_jobs(self) -> list[dict]:
        from app.models.assets import DownloadJob

        res = await self.s.execute(select(DownloadJob).order_by(DownloadJob.id.desc()))
        return [_job_to_dict(j) for j in res.scalars().all()]

    async def get_job(self, job_id: int) -> dict | None:
        from app.models.assets import DownloadJob

        job = await self.s.get(DownloadJob, job_id)
        return _job_to_dict(job) if job else None

    async def request_cancel(self, job_id: int) -> str | None:
        from app.models.assets import DownloadJob

        job = await self.s.get(DownloadJob, job_id)
        if job is None:
            return None
        job.cancel_requested = True
        if job.status in ("pending", "running"):
            job.status = "cancelled"
        task_id = job.celery_task_id
        await self.s.commit()
        return task_id

    async def clone_config(self, job_id: int) -> dict | None:
        from app.models.assets import DownloadJob

        job = await self.s.get(DownloadJob, job_id)
        if job is None:
            return None
        return {k: getattr(job, k) for k in CONFIG_KEYS}

    async def failed_asset_ids(self, job_id: int) -> list[str]:
        """Assets that failed while this job was the last to touch them (Lot 4)."""
        from app.models.assets import AssetStatus, DownloadedAsset

        res = await self.s.execute(
            select(DownloadedAsset.asset_id)
            .where(DownloadedAsset.last_job_id == job_id)
            .where(DownloadedAsset.status == AssetStatus.failed)
        )
        return [row[0] for row in res.all()]


def get_jobs_repo(session: AsyncSession = Depends(get_async_session)) -> SqlJobsRepository:
    return SqlJobsRepository(session)


def get_queue() -> JobQueue:
    return CeleryJobQueue()


# ------------------------------------------------------------------ endpoints
@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(body: CreateJobBody, repo=Depends(get_jobs_repo), queue=Depends(get_queue)) -> dict:
    if not body.selected_albums and not body.selected_asset_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Select at least one album or asset")
    job = await repo.create(body.model_dump())
    task_id = queue.enqueue(job["id"])
    await repo.set_task_id(job["id"], task_id)
    job["celery_task_id"] = task_id
    return job


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(repo=Depends(get_jobs_repo)) -> list:
    return await repo.list_jobs()


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: int, repo=Depends(get_jobs_repo)) -> dict:
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: int, repo=Depends(get_jobs_repo), queue=Depends(get_queue)) -> dict:
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    task_id = await repo.request_cancel(job_id)
    if task_id:
        queue.revoke(task_id)
    return {"cancelled": True}


class PreviewOut(BaseModel):
    """Dry-run result: what a job with this config would do (Lot 2)."""

    listed: int  # raw album listings walked (incl. duplicates across albums)
    matching: int  # unique assets passing the filters
    already_completed: int  # would be skipped (asset-ID-as-truth)
    to_download: int


class CompletedLookup(Protocol):
    async def completed_among(self, asset_ids: list[str]) -> int: ...


class SqlCompletedLookup:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def completed_among(self, asset_ids: list[str]) -> int:
        from sqlalchemy import func as safunc

        from app.models.assets import AssetStatus, DownloadedAsset

        total = 0
        for i in range(0, len(asset_ids), 1000):
            chunk = asset_ids[i : i + 1000]
            res = await self.s.execute(
                select(safunc.count())
                .select_from(DownloadedAsset)
                .where(DownloadedAsset.asset_id.in_(chunk))
                .where(DownloadedAsset.status == AssetStatus.completed)
            )
            total += int(res.scalar_one())
        return total


def get_completed_lookup(session: AsyncSession = Depends(get_async_session)) -> SqlCompletedLookup:
    return SqlCompletedLookup(session)


def _scan_matching(icloud, body: CreateJobBody) -> tuple[int, list[str]]:
    """Walk the selected albums applying the same filters the worker uses.
    Listing only — no downloads, no DB writes. Runs in a thread."""
    from app.workers.tasks import JobRecord, _passes_filter

    job = JobRecord(
        id=0,
        selected_albums=body.selected_albums,
        selected_asset_ids=body.selected_asset_ids,
        include_raw=body.include_raw,
        include_jpeg=body.include_jpeg,
        include_heic=body.include_heic,
        include_video=body.include_video,
        date_from=body.date_from,
        date_to=body.date_to,
    )
    wanted = set(body.selected_asset_ids or [])
    listed = 0
    seen: set[str] = set()
    matching: list[str] = []
    for album in body.selected_albums or []:
        for photo in icloud.iter_album(album):
            listed += 1
            aid = photo.id
            if aid in seen:
                continue
            seen.add(aid)
            if wanted and aid not in wanted:
                continue
            if not _passes_filter(photo, job):
                continue
            matching.append(aid)
    return listed, matching


@router.post("/jobs/preview", response_model=PreviewOut)
async def preview_job(
    body: CreateJobBody,
    service=Depends(get_service),
    lookup=Depends(get_completed_lookup),
) -> dict:
    if not body.selected_albums and not body.selected_asset_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Select at least one album or asset")
    listed, matching = await asyncio.to_thread(_scan_matching, service, body)
    completed = await lookup.completed_among(matching) if matching else 0
    to_download = len(matching) if body.force_redownload else len(matching) - completed
    return {
        "listed": listed,
        "matching": len(matching),
        "already_completed": completed,
        "to_download": to_download,
    }


@router.post("/jobs/{job_id}/retry-failed", response_model=JobOut,
             status_code=status.HTTP_201_CREATED)
async def retry_failed(job_id: int, repo=Depends(get_jobs_repo), queue=Depends(get_queue)) -> dict:
    spec = await repo.clone_config(job_id)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    # Precise retry (Lot 4): when the job link identifies the exact failures,
    # scope the new job to just those assets instead of re-walking everything.
    failed_ids = await repo.failed_asset_ids(job_id)
    if failed_ids:
        spec["selected_asset_ids"] = failed_ids
    job = await repo.create(spec)
    task_id = queue.enqueue(job["id"])
    await repo.set_task_id(job["id"], task_id)
    job["celery_task_id"] = task_id
    return job
