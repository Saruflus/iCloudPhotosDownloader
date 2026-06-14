"""Per-asset download orchestration (D5–D9).

Ties together ICloudService (fetch renditions), the path resolver (folder
template + fanout + collisions), EXIF, atomic writes, and the DB row. One asset
at a time; the Celery task (step 7) drives the loop, counters, and lock.

Everything external is injected (an `AssetStore`, a `ProgressPublisher`, the
`ICloudService`) so the whole flow is unit-testable with fakes + a temp dir —
no live Postgres/Redis/iCloud needed. Concrete SQLAlchemy/Redis impls are at the
bottom for the worker to wire in.
"""
from __future__ import annotations

import enum
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.paths import (
    AssetContext,
    album_targets,
    classify_media,
    disambiguate,
    final_path,
    with_suffix_name,
)
from app.services import exif

LOGGER = logging.getLogger(__name__)

TMP_DIRNAME = ".icloud-tmp"


class Outcome(str, enum.Enum):
    downloaded = "downloaded"
    skipped = "skipped"
    failed = "failed"


@dataclass
class JobSpec:
    """The subset of a download job the per-asset downloader needs."""

    template: list[str]
    download_version: str = "edited"  # edited | original | both
    album_fanout: bool = True
    force_redownload: bool = False


@dataclass
class StoredAsset:
    status: str
    files: list[dict] = field(default_factory=list)
    filename: str | None = None
    media_type: str | None = None
    created_at_icloud: datetime | None = None
    exif: dict = field(default_factory=dict)


class AssetStore(Protocol):
    def get(self, asset_id: str) -> StoredAsset | None: ...
    def begin(self, asset_id: str, *, filename: str, media_type: str | None,
              is_live: bool, source_version: str, job_id: int | None = None) -> None: ...
    def complete(self, asset_id: str, *, files: list[dict], exif: dict,
                 original_path: str | None, file_size: int | None,
                 created_at_icloud: datetime | None, source_version: str,
                 is_live: bool) -> None: ...
    def append_files(self, asset_id: str, files: list[dict]) -> None: ...
    def fail(self, asset_id: str, *, error: str) -> None: ...
    def path_owner(self, path: str) -> str | None: ...


class ProgressPublisher(Protocol):
    def publish(self, job_id: int | None, event: dict) -> None: ...


class _NullPublisher:
    def publish(self, job_id: int | None, event: dict) -> None:  # noqa: D401
        pass


class Downloader:
    def __init__(
        self,
        icloud,
        store: AssetStore,
        base_path: str | Path,
        publisher: ProgressPublisher | None = None,
        tz_name: str = "UTC",
    ) -> None:
        self.icloud = icloud
        self.store = store
        self.base = str(base_path)
        self.publisher = publisher or _NullPublisher()
        self.tz_name = tz_name

    # ------------------------------------------------------------------ main
    def download_asset(self, photo, albums: list[str], job: JobSpec,
                       job_id: int | None = None) -> Outcome:
        asset_id = photo.id
        filename = photo.filename

        stored = self.store.get(asset_id)
        if stored and stored.status == "completed" and not job.force_redownload:
            if job.album_fanout:
                added = self._reconcile_fanout(photo, stored, albums, job)
                if added:
                    self._log(job_id, "info", f"Fanned out {filename} to {added} new album(s)")
            return Outcome.skipped

        self.store.begin(
            asset_id,
            filename=filename,
            media_type=classify_media(Path(filename).suffix),
            is_live=bool(getattr(photo, "is_live_photo", False)),
            source_version=job.download_version,
            job_id=job_id,  # Lot 4: which job last touched this asset
        )
        tmp = self._tmp_dir(asset_id)
        try:
            renditions = self.icloud.download_asset(photo, job.download_version, tmp)
            if not renditions:
                raise RuntimeError("no renditions returned")

            exif_data = self._read_exif(renditions)
            capture = exif.resolve_capture_local(
                exif_data, getattr(photo, "created", None), self.tz_name
            )
            written = self._place(asset_id, photo, renditions, albums, exif_data, capture, job)

            primary = next((w for w in written if w["kind"] == "original"), written[0])
            self.store.complete(
                asset_id,
                files=written,
                exif=_exif_json(exif_data),
                original_path=primary["path"],
                file_size=primary.get("size"),
                created_at_icloud=getattr(photo, "created", None),
                source_version=job.download_version,
                is_live=bool(getattr(photo, "is_live_photo", False)),
            )
            self._log(job_id, "info", f"Downloaded {filename} → {os.path.dirname(primary['path'])}/")
            return Outcome.downloaded
        except Exception as exc:
            self.store.fail(asset_id, error=str(exc))
            self._log(job_id, "error", f"Failed {filename}: {exc}")
            return Outcome.failed
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # --------------------------------------------------------------- placing
    def _place(self, asset_id, photo, renditions, albums, exif_data, capture, job) -> list[dict]:
        ctx = self._context(photo, albums, exif_data, capture)
        targets = album_targets(job.template, ctx, job.album_fanout)
        has_both = (
            any(r.kind == "edited" for r in renditions)
            and any(r.kind == "original" for r in renditions)
        )
        written: list[dict] = []
        for r in renditions:
            if r.kind == "edited":
                name = with_suffix_name(photo.filename, "_edited" if has_both else "", r.ext)
            elif r.kind == "live_video":
                name = with_suffix_name(photo.filename, "_live", r.ext)
            else:
                name = with_suffix_name(photo.filename, "", r.ext)

            dests: list[tuple[Path, str | None]] = []
            for album in targets:
                fp = final_path(self.base, job.template, ctx, album, name)
                fp = disambiguate(fp, asset_id, self._taken(asset_id))
                dests.append((Path(str(fp)), album))

            self._materialize(Path(r.path), [d for d, _ in dests])
            for dest, album in dests:
                written.append({"path": str(dest), "kind": r.kind, "album": album, "size": r.size})
        return written

    def _reconcile_fanout(self, photo, stored: StoredAsset, albums, job) -> int:
        """Copy existing local files into album paths that appeared after the
        initial download — no iCloud re-download (D8)."""
        if not any("{album}" in seg for seg in job.template):
            return 0
        existing = {f.get("album") for f in (stored.files or [])}
        missing = [a for a in albums if a and a not in existing]
        if not missing:
            return 0
        capture = exif.resolve_capture_local(stored.exif or {}, stored.created_at_icloud, self.tz_name)
        ctx = AssetContext(
            filename=stored.filename or photo.filename,
            capture_dt=capture,
            albums=albums,
            persons=[],
            media_type=stored.media_type or classify_media(Path(photo.filename).suffix),
            make=(stored.exif or {}).get("make"),
            model=(stored.exif or {}).get("model"),
        )
        src_by_kind: dict[str, dict] = {}
        for f in stored.files or []:
            src_by_kind.setdefault(f["kind"], f)

        added: list[dict] = []
        for album in missing:
            for kind, srcf in src_by_kind.items():
                src = Path(srcf["path"])
                if not src.exists():
                    continue
                fp = final_path(self.base, job.template, ctx, album, src.name)
                fp = disambiguate(fp, photo.id, self._taken(photo.id))
                self._copy_atomic(src, Path(str(fp)))
                added.append({"path": str(fp), "kind": kind, "album": album, "size": srcf.get("size")})
        if added:
            self.store.append_files(photo.id, added)
        return len(added)

    # ----------------------------------------------------------- file system
    def _materialize(self, temp: Path, dests: list[Path]) -> None:
        """Atomically publish `temp` to one or more destinations (D9).

        Single dest → move; multiple (fanout) → copy to all but the last, move
        the last. Each publish is `*.part` + fsync + atomic rename.
        """
        for i, dest in enumerate(dests):
            last = i == len(dests) - 1
            if last:
                self._move_atomic(temp, dest)
            else:
                self._copy_atomic(temp, dest)

    def _move_atomic(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        os.replace(src, part)  # same-fs move
        self._fsync(part)
        os.replace(part, dest)

    def _copy_atomic(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(src, part)
        self._fsync(part)
        os.replace(part, dest)

    @staticmethod
    def _fsync(path: Path) -> None:
        try:
            fd = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    # --------------------------------------------------------------- helpers
    def _context(self, photo, albums, exif_data, capture) -> AssetContext:
        return AssetContext(
            filename=photo.filename,
            capture_dt=capture,
            albums=list(albums),
            persons=[],  # pyicloud doesn't reliably expose face tags (note 10)
            media_type=classify_media(Path(photo.filename).suffix),
            make=exif_data.get("make"),
            model=exif_data.get("model"),
        )

    def _read_exif(self, renditions) -> dict:
        source = next((r for r in renditions if r.kind == "original"), None) \
            or next((r for r in renditions if r.kind == "edited"), None)
        return exif.extract(source.path) if source else {}

    def _taken(self, asset_id: str):
        def is_taken(path: PurePosixPath) -> bool:
            owner = self.store.path_owner(str(path))
            return owner is not None and owner != asset_id
        return is_taken

    def _tmp_dir(self, asset_id: str) -> Path:
        safe = asset_id.replace("/", "_")
        tmp = Path(self.base) / TMP_DIRNAME / safe
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp

    def _log(self, job_id, level, message) -> None:
        self.publisher.publish(job_id, {"type": "log", "level": level, "message": message})


def _exif_json(exif_data: dict) -> dict:
    out: dict = {}
    for k, v in exif_data.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


# ============================================================ concrete impls
class SqlAssetStore:
    """SQLAlchemy-backed AssetStore (sync session, D1). Used by the worker."""

    def __init__(self, session) -> None:
        self.s = session

    def get(self, asset_id):
        from app.models.assets import DownloadedAsset

        row = self.s.query(DownloadedAsset).filter_by(asset_id=asset_id).one_or_none()
        if row is None:
            return None
        return StoredAsset(
            status=row.status.value if hasattr(row.status, "value") else str(row.status),
            files=row.files or [],
            filename=row.filename,
            media_type=row.media_type,
            created_at_icloud=row.created_at_icloud,
            exif=row.exif_data or {},
        )

    def begin(self, asset_id, *, filename, media_type, is_live, source_version, job_id=None):
        from app.models.assets import AssetStatus, DownloadedAsset

        row = self.s.query(DownloadedAsset).filter_by(asset_id=asset_id).one_or_none()
        if row is None:
            row = DownloadedAsset(asset_id=asset_id)
            self.s.add(row)
        row.filename = filename
        row.media_type = media_type
        row.is_live_photo = is_live
        row.source_version = source_version
        row.status = AssetStatus.downloading
        row.error_message = None
        if job_id is not None:
            row.last_job_id = job_id
        self.s.commit()

    def complete(self, asset_id, *, files, exif, original_path, file_size,
                 created_at_icloud, source_version, is_live):
        from app.models.assets import AssetStatus, DownloadedAsset

        row = self.s.query(DownloadedAsset).filter_by(asset_id=asset_id).one()
        row.files = files
        row.exif_data = exif
        row.original_path = original_path
        row.file_size = file_size
        row.created_at_icloud = created_at_icloud
        row.source_version = source_version
        row.is_live_photo = is_live
        row.downloaded_at = datetime.now(timezone.utc)
        row.status = AssetStatus.completed
        self.s.commit()

    def append_files(self, asset_id, files):
        from app.models.assets import DownloadedAsset

        row = self.s.query(DownloadedAsset).filter_by(asset_id=asset_id).one()
        row.files = (row.files or []) + files
        self.s.commit()

    def fail(self, asset_id, *, error):
        from app.models.assets import AssetStatus, DownloadedAsset

        row = self.s.query(DownloadedAsset).filter_by(asset_id=asset_id).one_or_none()
        if row is None:
            return
        row.status = AssetStatus.failed
        row.error_message = error[:1000]
        row.retry_count = (row.retry_count or 0) + 1
        self.s.commit()

    def path_owner(self, path):
        from sqlalchemy import text

        sql = text(
            "SELECT asset_id FROM downloaded_assets "
            "WHERE files @> :needle ::jsonb LIMIT 1"
        )
        import json as _json

        row = self.s.execute(sql, {"needle": _json.dumps([{"path": path}])}).first()
        return row[0] if row else None

    # ---------------------------------------------------- verify mode (Lot 4)
    def iter_completed(self):
        """Yield (asset_id, files) for every completed asset (chunked)."""
        from app.models.assets import AssetStatus, DownloadedAsset

        q = (
            self.s.query(DownloadedAsset.asset_id, DownloadedAsset.files)
            .filter(DownloadedAsset.status == AssetStatus.completed)
            .yield_per(500)
        )
        for asset_id, files in q:
            yield asset_id, files or []

    def reset_to_pending(self, asset_ids):
        from app.models.assets import AssetStatus, DownloadedAsset

        for i in range(0, len(asset_ids), 1000):
            chunk = asset_ids[i : i + 1000]
            (
                self.s.query(DownloadedAsset)
                .filter(DownloadedAsset.asset_id.in_(chunk))
                .update({DownloadedAsset.status: AssetStatus.pending},
                        synchronize_session=False)
            )
        self.s.commit()

    def touch_verified(self, asset_ids):
        from app.models.assets import DownloadedAsset

        now = datetime.now(timezone.utc)
        for i in range(0, len(asset_ids), 1000):
            chunk = asset_ids[i : i + 1000]
            (
                self.s.query(DownloadedAsset)
                .filter(DownloadedAsset.asset_id.in_(chunk))
                .update({DownloadedAsset.last_verified_at: now},
                        synchronize_session=False)
            )
        self.s.commit()


class RedisProgressPublisher:
    """Publishes progress to a Redis channel + keeps a 100-line replay log (note 4)."""

    def __init__(self, redis_client, log_max: int = 100) -> None:
        self.r = redis_client
        self.log_max = log_max

    def publish(self, job_id, event):
        import json as _json

        if job_id is None:
            return
        payload = _json.dumps(event)
        self.r.publish(f"icloud:job:{job_id}:progress", payload)
        if event.get("type") == "log":
            key = f"icloud:job:{job_id}:log"
            self.r.lpush(key, payload)
            self.r.ltrim(key, 0, self.log_max - 1)
