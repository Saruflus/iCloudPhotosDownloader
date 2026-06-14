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
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Iterator

from pyicloud import PyiCloudService
from pyicloud.services.photos_cloudkit.mappers import build_photo_resource, record_field_value

from app.core.paths import classify_media

LOGGER = logging.getLogger(__name__)

EDITED_RES_FIELD = "resJPEGFullRes"  # present in the asset record iff edited (D6)
EDITED_PREFIX = "resJPEGFull"
RAW_RES_FIELD = "resOriginalAltRes"  # present in the master record iff a RAW companion exists
_STREAM_CHUNK = 1 << 20  # 1 MiB


@dataclass
class AssetMetadata:
    asset_id: str
    filename: str
    media_type: str | None  # raw uppercase extension, e.g. "RAF" (display)
    media_category: str | None  # classify_media bucket: "RAW"/"Video"/"HEIC"/"JPEG"/…
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


class ThrottleGate:
    """Global cooldown when Apple rate-limits (Lot 3).

    Shared by every download thread in the process: one 429/503 pauses them all
    instead of each thread hammering and re-tripping the limit. Exponential
    cooldown (base→2x→…→cap), reset after the first success. `sleep`/`now` are
    injectable for tests.
    """

    def __init__(self, base: float = 30.0, cap: float = 600.0,
                 sleep=time.sleep, now=time.monotonic) -> None:
        self.base = base
        self.cap = cap
        self._sleep = sleep
        self._now = now
        self._lock = threading.Lock()
        self._until = 0.0
        self._delay = 0.0

    def wait(self) -> None:
        """Block until any active cooldown has elapsed."""
        while True:
            with self._lock:
                remaining = self._until - self._now()
            if remaining <= 0:
                return
            self._sleep(min(remaining, 5.0))  # re-check; another thread may extend

    def trip(self) -> float:
        """Record a throttle response; returns the cooldown now in effect."""
        with self._lock:
            self._delay = min(self.cap, max(self.base, self._delay * 2))
            self._until = max(self._until, self._now() + self._delay)
            return self._delay

    def clear(self) -> None:
        """A request succeeded — drop the escalation level."""
        with self._lock:
            self._delay = 0.0
            self._until = 0.0


THROTTLE = ThrottleGate()


def _is_throttle_error(exc: Exception) -> bool:
    """429/503 from requests (.response.status_code) or pyicloud (.code)."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    try:
        return int(code) in (429, 503)
    except (TypeError, ValueError):
        return False


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
    def get_albums(self, with_counts: bool = True) -> list[dict]:
        """Album list (personal + shared streams). ``with_counts=False`` skips
        ``len(album)`` — each count is its own iCloud query, so 40 albums = 40
        round-trips; the API serves counts lazily via ``get_album_count``."""
        out = [
            {"name": album.name, "asset_count": _safe_len(album) if with_counts else None,
             "shared": False}
            for album in self._require().photos.albums
        ]
        for album in self._shared_albums():
            out.append({
                "name": album.name,
                "asset_count": _safe_len(album) if with_counts else None,
                "shared": True,
            })
        return out

    def get_album_count(self, album_name: str) -> int | None:
        return _safe_len(self._resolve_album(album_name))

    def _shared_albums(self) -> list:
        """Shared photo streams; [] when the endpoint is unavailable (the legacy
        sharedstreams API can 5xx independently of the main library)."""
        try:
            return list(self._require().photos.shared_streams)
        except Exception as exc:
            LOGGER.warning("Shared albums unavailable: %s", exc)
            return []

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
        THROTTLE.wait()
        return _as_bytes(photo.download("thumb"))

    def fetch_asset(self, asset_id: str):
        """Fetch a single PhotoAsset by id straight from iCloud (Lot 3).

        Used when the in-process cache is cold (e.g. after a restart) so the
        thumbnail endpoint no longer 404s until the album is re-browsed. Returns
        None when the asset can't be found/looked up.
        """
        try:
            photo = self._require().photos.all._get_photo(asset_id)
        except Exception as exc:  # KeyError for unknown ids; network errors
            LOGGER.info("Asset lookup failed for %s: %s", asset_id, exc)
            return None
        if photo is not None:
            self._cache_asset(photo)
        return photo

    def thumbnail_for(self, asset_id: str) -> bytes | None:
        """Thumbnail bytes for an asset; falls back to a direct iCloud lookup
        when the asset wasn't listed in this process (restart survivor)."""
        photo = self._asset_cache.get(asset_id)
        if photo is None:
            photo = self.fetch_asset(asset_id)
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
            if album is None:  # personal albums win on a name collision
                album = next((a for a in self._shared_albums() if a.name == album_name), None)
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

        Mirrors ``_has_edited``: read the single discriminator field straight off
        the record rather than going through ``photo.versions``. ``versions``
        eagerly builds *every* rendition resource, so one unrelated build failure
        would be swallowed and silently suppress the badge. The RAW token lives in
        the *master* record (``resOriginalAltRes``); the typed CloudKit album query
        sends no desiredKeys (server returns all fields) and the legacy path uses
        PHOTO_DESIRED_KEYS which includes it — present at listing time, no network.
        ``record_field_value`` handles both typed CKRecord and legacy-dict records.
        ``versions`` stays as a guarded fallback for any record shape we missed.
        """
        rec = getattr(photo, "_master_record", None)
        if rec is not None:
            try:
                if record_field_value(rec, RAW_RES_FIELD) is not None:
                    return True
            except Exception:
                pass
        try:
            return "alternative" in (photo.versions or {})
        except Exception:
            return False

    @staticmethod
    def _media_category(photo) -> str | None:
        """classify_media bucket for the asset, item_type-aware for videos.

        Single source of truth shared with the worker's download filter — the UI
        must not keep its own extension lists (they drift; classify_media knows
        16 RAW extensions, the old frontend list had 8).
        """
        if getattr(photo, "item_type", None) == "movie":
            return "Video"
        fn = getattr(photo, "filename", None)
        if not fn or "." not in fn:
            return None
        return classify_media(fn.rsplit(".", 1)[-1])

    def _metadata(self, photo) -> AssetMetadata:
        return AssetMetadata(
            asset_id=photo.id,
            filename=photo.filename,
            media_type=_media_type(photo.filename),
            media_category=self._media_category(photo),
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
        THROTTLE.wait()  # global cooldown — one 429 pauses every worker thread
        try:
            resp = self._require().photos.session.get(url, stream=True)
            resp.raise_for_status()
        except Exception as exc:
            if _is_throttle_error(exc):
                delay = THROTTLE.trip()
                LOGGER.warning("Apple throttling (429/503); cooling down %.0fs", delay)
            raise
        total = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=_STREAM_CHUNK):
                if chunk:
                    fh.write(chunk)
                    total += len(chunk)
        THROTTLE.clear()  # back to normal after a success
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
