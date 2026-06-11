"""iCloud service (D12) — the single chokepoint for all iCloud access.

Engine (D12): the original `pyicloud` library (modern 2FA). icloudpd is binary-only
and not importable; pyicloud-ipd is stale. Everything else in the app goes through
this class, so the engine stays swappable.

Edited renditions (D6, confirmed against a live account in the step-2 spike):
pyicloud's built-in `versions` only expose master-record renditions
(original/medium/thumb). The EDITED full-res lives in the *asset* record as
`resJPEGFull`. An asset is "edited" iff `resJPEGFullRes` is present in the asset
record — the `adjustment*` fields are NOT reliable (they exist on unedited assets).
We reuse pyicloud's own `build_photo_resource` against the asset record to get the
edited resource, then stream it via the photos session.

pyicloud is blocking (`requests`); FastAPI callers must wrap these calls in
`asyncio.to_thread` (D1). Within the Celery worker they run synchronously.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Iterator

from pyicloud import PyiCloudService
from pyicloud.services.photos_cloudkit.mappers import build_photo_resource

LOGGER = logging.getLogger(__name__)

EDITED_RES_FIELD = "resJPEGFullRes"  # present in the asset record iff edited (D6)
EDITED_PREFIX = "resJPEGFull"
_STREAM_CHUNK = 1 << 20  # 1 MiB


@dataclass
class AssetMetadata:
    asset_id: str
    filename: str
    media_type: str | None
    file_size: int | None
    created_at: datetime | None  # tz-aware
    is_live_photo: bool
    has_edited_version: bool
    has_raw_version: bool


@dataclass
class DownloadedFile:
    path: Path  # temp file, named with the real extension
    kind: str  # "original" | "edited" | "live_video"
    size: int
    ext: str = ""  # rendition extension, e.g. ".HEIC" / ".MOV"


class ICloudError(RuntimeError):
    """Raised for auth/lookup failures in the iCloud layer."""


class ICloudService:
    """Wraps pyicloud. Construct with the cookie dir; auth lazily."""

    def __init__(self, cookie_dir: str | Path, asset_cache_size: int = 10000) -> None:
        self._cookie_dir = str(cookie_dir)
        self._api: PyiCloudService | None = None
        # Bounded {asset_id -> PhotoAsset} cache so the stateless thumbnail
        # endpoint can find an asset that a recent listing already fetched.
        self._asset_cache: "OrderedDict[str, object]" = OrderedDict()
        self._asset_cache_size = asset_cache_size

    # ------------------------------------------------------------------ auth
    def authenticate(self, apple_id: str, password: str) -> bool:
        """Build the session. Returns True if 2FA is still required (D2)."""
        Path(self._cookie_dir).mkdir(parents=True, exist_ok=True)
        self._api = PyiCloudService(
            apple_id, password, cookie_directory=self._cookie_dir
        )
        self._remember_apple_id(apple_id)  # non-secret; enables passwordless restore
        return bool(self._api.requires_2fa)

    def submit_2fa(self, code: str) -> bool:
        api = self._require()
        if not api.validate_2fa_code(code):
            return False
        if not api.is_trusted_session:
            api.trust_session()
        return True

    def try_restore(self) -> bool:
        """Best-effort passwordless restore from the cookie dir (D2).

        Verified: with a valid trusted session and the remembered Apple ID,
        pyicloud reconstructs an authenticated session without the password or a
        new 2FA prompt. Returns True if authenticated. Network-free if no Apple
        ID was remembered.
        """
        apple_id = self.remembered_apple_id()
        if not apple_id:
            return False
        try:
            api = PyiCloudService(apple_id, cookie_directory=self._cookie_dir)
            if api.requires_2fa:
                return False
            self._api = api
            return True
        except Exception as exc:  # expired/invalid session, network, etc.
            LOGGER.warning("iCloud session restore failed: %s", exc)
            return False

    def logout(self) -> None:
        """Drop the in-process session and clear the on-disk cookie dir (D2)."""
        self._api = None
        cookie_dir = Path(self._cookie_dir)
        if cookie_dir.is_dir():
            for entry in cookie_dir.iterdir():
                if entry.is_file():
                    try:
                        entry.unlink()
                    except OSError as exc:
                        LOGGER.warning("Could not remove %s: %s", entry, exc)

    # apple-id persistence (the id is not secret; the session cookie is)
    def _apple_id_path(self) -> Path:
        return Path(self._cookie_dir) / ".apple_id"

    def _remember_apple_id(self, apple_id: str) -> None:
        try:
            self._apple_id_path().write_text(apple_id, encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("Could not persist apple_id: %s", exc)

    def remembered_apple_id(self) -> str | None:
        path = self._apple_id_path()
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
        return None

    def get_status(self) -> dict:
        api = self._api
        if api is None:
            return {"authenticated": False, "needs_2fa": False}
        needs = bool(api.requires_2fa)
        return {"authenticated": not needs, "needs_2fa": needs}

    @property
    def api(self) -> PyiCloudService:
        """Underlying pyicloud handle (for advanced use / verification)."""
        return self._require()

    # -------------------------------------------------------------- browsing
    def get_albums(self) -> list[dict]:
        return [
            {"name": album.name, "asset_count": _safe_len(album)}
            for album in self._require().photos.albums
        ]

    def get_assets(
        self, album_name: str | None, offset: int = 0, limit: int = 50
    ) -> list[AssetMetadata]:
        album = self._resolve_album(album_name)
        out: list[AssetMetadata] = []
        for photo in islice(album, offset, offset + limit):
            self._cache_asset(photo)  # so the thumbnail endpoint can find it
            out.append(self._metadata(photo))
        return out

    def iter_album(self, album_name: str | None) -> Iterator:
        """Yield raw PhotoAsset objects (the download worker consumes these, D13)."""
        return iter(self._resolve_album(album_name))

    def get_asset_thumbnail(self, photo) -> bytes:
        return _as_bytes(photo.download("thumb"))

    def thumbnail_for(self, asset_id: str) -> bytes | None:
        """Thumbnail bytes for a previously-listed asset, or None on cache miss."""
        photo = self._asset_cache.get(asset_id)
        if photo is None:
            return None
        return self.get_asset_thumbnail(photo)

    def _cache_asset(self, photo) -> None:
        aid = photo.id
        self._asset_cache[aid] = photo
        self._asset_cache.move_to_end(aid)
        while len(self._asset_cache) > self._asset_cache_size:
            self._asset_cache.popitem(last=False)

    # -------------------------------------------------------------- download
    def download_asset(self, photo, version: str, tmp_dir: Path) -> list[DownloadedFile]:
        """Stream the requested rendition(s) to ``*.part`` temp files (D6, D9).

        Returns possibly multiple files: original and/or edited, plus the Live
        Photo video component. The downloader decides final placement.
        """
        tmp_dir = Path(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        files: list[DownloadedFile] = []

        edited = self._has_edited(photo)
        want_original = version in ("original", "both") or (version == "edited" and not edited)
        want_edited = version in ("edited", "both") and edited

        if want_original:
            files.append(self._download_master(photo, "original", "original", tmp_dir))
        if want_edited:
            f = self._download_edited(photo, tmp_dir)
            if f is not None:
                files.append(f)

        if bool(getattr(photo, "is_live_photo", False)):
            versions = photo.versions or {}
            for vkey in ("original_video", "sidecar"):
                if vkey in versions:
                    files.append(self._download_master(photo, vkey, "live_video", tmp_dir))
                    break

        return files

    # ------------------------------------------------------------- internals
    def _require(self) -> PyiCloudService:
        if self._api is None:
            raise ICloudError("iCloud session not authenticated")
        return self._api

    def _resolve_album(self, album_name: str | None):
        api = self._require()
        if album_name:
            album = api.photos.albums.find(album_name)
            if album is None:
                raise ICloudError(f"Album not found: {album_name}")
            return album
        return api.photos.all

    @staticmethod
    def _has_edited(photo) -> bool:
        rec = getattr(photo, "_asset_record", None)
        if rec is None:
            return False
        try:
            return rec.fields.get_value(EDITED_RES_FIELD) is not None
        except Exception:
            return False

    @staticmethod
    def _has_raw(photo) -> bool:
        """True iff the asset has a RAW companion (resOriginalAlt, D6).

        pyicloud's PHOTO_VERSION_LOOKUP maps the friendly key 'alternative' to the
        resOriginalAlt prefix; the resource is only present in `versions` when the
        master record actually carries that rendition. No network — `versions` is
        built from the already-fetched CK records.
        """
        try:
            return "alternative" in (photo.versions or {})
        except Exception:
            return False

    def _metadata(self, photo) -> AssetMetadata:
        return AssetMetadata(
            asset_id=photo.id,
            filename=photo.filename,
            media_type=_media_type(photo.filename),
            file_size=getattr(photo, "size", None),
            created_at=getattr(photo, "created", None),
            is_live_photo=bool(getattr(photo, "is_live_photo", False)),
            has_edited_version=self._has_edited(photo),
            has_raw_version=self._has_raw(photo),
        )

    def _rendition_ext(self, photo, version: str) -> str:
        """Extension for a master rendition, from its resource filename."""
        try:
            fn = (photo.versions or {}).get(version, {}).get("filename")
            if fn and "." in fn:
                return "." + fn.rsplit(".", 1)[-1]
        except Exception:
            pass
        return Path(photo.filename).suffix or ".bin"

    def _download_master(self, photo, version, kind, tmp_dir) -> DownloadedFile:
        url = photo.download_url(version)
        if url is None:
            raise ICloudError(f"No '{version}' rendition for asset {photo.id}")
        ext = self._rendition_ext(photo, version)
        dest = tmp_dir / f"{photo.id}.{kind}{ext}"
        size = self._stream(url, dest)
        return DownloadedFile(path=dest, kind=kind, size=size, ext=ext)

    def _download_edited(self, photo, tmp_dir) -> DownloadedFile | None:
        # The edited rendition token lives in the asset record (D6).
        resource = build_photo_resource(
            key="edited",
            prefix=EDITED_PREFIX,
            master_record=photo._asset_record,
            filename=photo.filename,
            item_type_extensions=photo.FILE_TYPE_EXTENSIONS,
            is_live_photo=bool(getattr(photo, "is_live_photo", False)),
            item_type_lookup=photo.ITEM_TYPES,
        )
        if resource is None or not resource.url:
            return None
        fn = getattr(resource, "filename", None) or photo.filename
        ext = ("." + fn.rsplit(".", 1)[-1]) if "." in fn else (Path(photo.filename).suffix or ".bin")
        dest = tmp_dir / f"{photo.id}.edited{ext}"
        size = self._stream(resource.url, dest)
        return DownloadedFile(path=dest, kind="edited", size=size, ext=ext)

    def _stream(self, url: str, dest: Path) -> int:
        resp = self._require().photos.session.get(url, stream=True)
        resp.raise_for_status()
        total = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
        return total


# ---------------------------------------------------------------- helpers
def _safe_len(album) -> int | None:
    try:
        return len(album)
    except Exception:
        return None


def _as_bytes(data) -> bytes:
    raw = getattr(data, "raw", None)
    if raw is not None:
        return raw.read()
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return b"" if data is None else bytes(data)


def _media_type(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].upper()


@lru_cache
def get_icloud_service() -> ICloudService:
    """Process-wide singleton, built from settings (D2).

    One instance per process holds the live pyicloud session. FastAPI (single
    worker), the Celery worker, and the scheduler each get their own, all sharing
    the trusted session via the `/config` cookie dir.
    """
    from app.core.config import get_settings

    return ICloudService(get_settings().icloud_config_dir)
