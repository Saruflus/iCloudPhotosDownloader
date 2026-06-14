"""SQLAlchemy-backed JobStore (sync session, D1). Used by the Celery worker."""
from __future__ import annotations

from app.workers.tasks import JobRecord


class SqlJobStore:
    def __init__(self, session) -> None:
        self.s = session

    def get(self, job_id: int) -> JobRecord | None:
        from app.models.assets import DownloadJob

        j = self.s.get(DownloadJob, job_id)
        if j is None:
            return None
        return JobRecord(
            id=j.id,
            selected_albums=j.selected_albums or [],
            selected_asset_ids=j.selected_asset_ids or [],
            folder_structure=j.folder_structure or [],
            include_raw=j.include_raw,
            include_jpeg=j.include_jpeg,
            include_heic=j.include_heic,
            include_video=j.include_video,
            download_version=j.download_version,
            album_fanout=j.album_fanout,
            force_redownload=j.force_redownload,
            date_from=j.date_from,
            date_to=j.date_to,
            job_type=getattr(j, "job_type", "download") or "download",
        )

    def set_status(self, job_id: int, status: str) -> None:
        from app.models.assets import DownloadJob

        j = self.s.get(DownloadJob, job_id)
        if j is not None:
            j.status = status
            self.s.commit()

    def set_total(self, job_id: int, total: int) -> None:
        from app.models.assets import DownloadJob

        j = self.s.get(DownloadJob, job_id)
        if j is not None:
            j.total_assets = total
            self.s.commit()

    def update_counts(self, job_id: int, *, downloaded: int, skipped: int, failed: int) -> None:
        from app.models.assets import DownloadJob

        j = self.s.get(DownloadJob, job_id)
        if j is not None:
            j.downloaded_count = downloaded
            j.skipped_count = skipped
            j.failed_count = failed
            self.s.commit()

    def is_cancel_requested(self, job_id: int) -> bool:
        from app.models.assets import DownloadJob

        self.s.expire_all()  # cancel may be set by the API in another process
        j = self.s.get(DownloadJob, job_id)
        return bool(j.cancel_requested) if j is not None else False
