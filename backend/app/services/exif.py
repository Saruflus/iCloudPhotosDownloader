"""Format-aware EXIF extraction (D5).

`piexif` alone can't read HEIC or RAW (the v1 plan bug). We dispatch by format:
  JPEG/TIFF -> piexif
  HEIC/HEIF -> pillow-heif + Pillow
  RAW       -> exifread
Each backend is imported lazily inside its branch, so importing this module (and
unit-testing the path resolver) never requires the heavy image libs. Extraction
is best-effort: anything unreadable yields {} rather than raising.

We only normalize the few fields the folder tokens need:
  datetime_original (naive datetime, local-as-shot), make, model.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)

_JPEG = {".jpg", ".jpeg", ".tif", ".tiff"}
_HEIC = {".heic", ".heif"}
_RAW = {
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".rw2", ".orf",
    ".pef", ".srw", ".sr2", ".3fr", ".erf", ".kdc", ".nrw", ".raw",
}


def _parse_exif_dt(value: str | bytes | None) -> datetime | None:
    """Parse an EXIF 'YYYY:MM:DD HH:MM:SS' timestamp (naive, local-as-shot)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = value.strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _from_jpeg(path: Path) -> dict:
    import piexif

    data = piexif.load(str(path))
    exif_ifd = data.get("Exif", {})
    zeroth = data.get("0th", {})
    return {
        "datetime_original": _parse_exif_dt(exif_ifd.get(piexif.ExifIFD.DateTimeOriginal)),
        "make": _clean(zeroth.get(piexif.ImageIFD.Make)),
        "model": _clean(zeroth.get(piexif.ImageIFD.Model)),
    }


def _from_heic(path: Path) -> dict:
    import pillow_heif

    pillow_heif.register_heif_opener()
    from PIL import Image

    with Image.open(path) as img:
        exif = img.getexif()
    return {
        "datetime_original": _parse_exif_dt(exif.get(0x9003) or exif.get(0x0132)),
        "make": _clean(exif.get(0x010F)),
        "model": _clean(exif.get(0x0110)),
    }


def _from_raw(path: Path) -> dict:
    import exifread

    with open(path, "rb") as fh:
        tags = exifread.process_file(fh, details=False)
    dt = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
    return {
        "datetime_original": _parse_exif_dt(str(dt) if dt else None),
        "make": _clean(str(tags["Image Make"])) if "Image Make" in tags else None,
        "model": _clean(str(tags["Image Model"])) if "Image Model" in tags else None,
    }


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip().strip("\x00").strip()
    return value or None


def extract(path: str | Path) -> dict:
    """Best-effort normalized EXIF dict. Never raises."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext in _JPEG:
            result = _from_jpeg(path)
        elif ext in _HEIC:
            result = _from_heic(path)
        elif ext in _RAW:
            result = _from_raw(path)
        else:
            return {}
    except Exception as exc:  # missing lib, corrupt file, unexpected layout
        LOGGER.debug("EXIF extraction failed for %s: %s", path, exc)
        return {}
    return {k: v for k, v in result.items() if v is not None}


def resolve_capture_local(
    exif: dict, icloud_created: datetime | None, tz_name: str
) -> datetime | None:
    """Pick the capture time and return it in the user's local tz (naive).

    Prefer EXIF DateTimeOriginal (already local-as-shot); else fall back to the
    iCloud created timestamp (UTC) converted to `tz_name` (D5 / timezone fix).
    """
    dt = exif.get("datetime_original")
    if dt is not None:
        return dt  # EXIF time is already in the camera's local time
    if icloud_created is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    aware = icloud_created if icloud_created.tzinfo else icloud_created.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz).replace(tzinfo=None)
