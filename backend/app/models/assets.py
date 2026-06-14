"""ORM models for the iCloud → NAS sync app.

Core principle: ``DownloadedAsset.asset_id`` is the source of truth. Once
``status == completed`` the asset is never re-downloaded (unless a job sets
``force_redownload``), regardless of where the file is later moved on disk.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum as SAEnum,
    Integer,
    String,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetStatus(str, enum.Enum):
    pending = "pending"
    downloading = "downloading"
    completed = "completed"
    failed = "failed"


class DownloadedAsset(Base):
    """One row per iCloud asset we have (or are) downloading."""

    __tablename__ = "downloaded_assets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str | None] = mapped_column(String, index=True)
    is_live_photo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # D6
    source_version: Mapped[str | None] = mapped_column(String)  # D6: edited/original
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    created_at_icloud: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), index=True
    )
    albums: Mapped[list | None] = mapped_column(JSONB)
    persons: Mapped[list | None] = mapped_column(JSONB)
    exif_data: Mapped[dict | None] = mapped_column(JSONB)
    # [{path, kind, album, size}] — multiple files per asset (D6, D8)
    files: Mapped[list | None] = mapped_column(JSONB)
    original_path: Mapped[str | None] = mapped_column(String)  # primary file, informational
    status: Mapped[AssetStatus] = mapped_column(
        SAEnum(AssetStatus, name="asset_status"),
        nullable=False,
        default=AssetStatus.pending,
        index=True,
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The job that last wrote this asset (Lot 4 link; informational + retry scoping).
    last_job_id: Mapped[int | None] = mapped_column(BigInteger, index=True)


class AppSetting(Base):
    """Runtime-overridable settings (Lot 2 settings page), one row per key.

    Only a whitelisted subset of Settings can be overridden here; everything
    else stays env-driven. Workers re-read overrides when each job starts."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | None] = mapped_column(JSONB)


class DownloadJob(Base):
    """One row per user-initiated or scheduled download run."""

    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    selected_albums: Mapped[list | None] = mapped_column(JSONB)
    selected_asset_ids: Mapped[list | None] = mapped_column(JSONB)
    folder_structure: Mapped[list | None] = mapped_column(JSONB)
    include_raw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_jpeg: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_heic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    download_version: Mapped[str] = mapped_column(String, nullable=False, default="edited")  # D6
    album_fanout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # D8
    force_redownload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Only assets captured inside [date_from, date_to] (either side optional).
    date_from: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    date_to: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # 'download' (normal) or 'verify' (re-download files missing on disk, Lot 4).
    job_type: Mapped[str] = mapped_column(String, nullable=False, default="download")
    total_assets: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloaded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    celery_task_id: Mapped[str | None] = mapped_column(String)


class Schedule(Base):
    """A cron schedule that fires download jobs from a saved config."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cron_expression: Mapped[str] = mapped_column(String, nullable=False)
    job_config: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
