"""Folder-structure path resolver (D5, D7, D8).

Pure logic — no iCloud, DB, or filesystem reads (collision checks are injected as
a predicate so this stays unit-testable). The downloader (step 6) composes these
building blocks: classify media, resolve token segments into a sanitized relative
dir, expand album fanout, and disambiguate on-disk collisions.

A folder template is a JSON array mixing tokens and plain strings, e.g.
    ["{year}", "{month}", "Photos", "{album}"]
Tokens: {year} {month} {day} {album} {person} {mediatype} {make} {model} {filename}
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

UNKNOWN = "Unknown"
UNDATED = "Undated"
_MAX_SEGMENT = 120

# Characters illegal/unsafe in a path segment (covers Linux + Windows + control chars).
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TOKEN = re.compile(r"\{([a-z]+)\}")

RAW_EXTS = {
    "cr2", "cr3", "nef", "arw", "dng", "raf", "rw2", "orf",
    "pef", "srw", "sr2", "3fr", "erf", "kdc", "nrw", "raw",
}
VIDEO_EXTS = {"mov", "mp4", "m4v", "avi", "hevc", "3gp", "3g2", "mpg", "mpeg"}
HEIC_EXTS = {"heic", "heif"}
JPEG_EXTS = {"jpg", "jpeg"}


def classify_media(ext: str) -> str:
    """Map a file extension to a {mediatype} token value."""
    e = ext.lower().lstrip(".")
    if e in RAW_EXTS:
        return "RAW"
    if e in VIDEO_EXTS:
        return "Video"
    if e in HEIC_EXTS:
        return "HEIC"
    if e in JPEG_EXTS:
        return "JPEG"
    return e.upper() if e else UNKNOWN


@dataclass
class AssetContext:
    """Everything the resolver needs to fill tokens for one asset.

    `capture_dt` must already be in the user's local timezone (the downloader
    localizes it: EXIF DateTimeOriginal is local-as-shot; the iCloud fallback is
    converted from UTC). `media_type` is a {mediatype} value (see classify_media).
    """

    filename: str  # original, e.g. "IMG_0042.HEIC"
    capture_dt: datetime | None = None
    albums: list[str] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    media_type: str = UNKNOWN
    make: str | None = None
    model: str | None = None


def _token_value(token: str, ctx: AssetContext, album: str | None) -> str:
    dt = ctx.capture_dt
    if token == "year":
        return dt.strftime("%Y") if dt else UNDATED
    if token == "month":
        return dt.strftime("%m") if dt else UNDATED
    if token == "day":
        return dt.strftime("%d") if dt else UNDATED
    if token == "album":
        return album or (ctx.albums[0] if ctx.albums else UNKNOWN)
    if token == "person":
        return ctx.persons[0] if ctx.persons else UNKNOWN
    if token == "mediatype":
        return ctx.media_type or UNKNOWN
    if token == "make":
        return ctx.make or UNKNOWN
    if token == "model":
        return ctx.model or UNKNOWN
    if token == "filename":
        return PurePosixPath(ctx.filename).stem
    return "{" + token + "}"  # unknown token: leave literal (sanitized later)


def _substitute(segment: str, ctx: AssetContext, album: str | None) -> str:
    return _TOKEN.sub(lambda m: _token_value(m.group(1), ctx, album), segment)


def sanitize_segment(value: str) -> str:
    """Make a single path segment safe: strip illegal chars, block traversal."""
    s = _ILLEGAL.sub("_", value).strip()
    while ".." in s:
        s = s.replace("..", "_")
    s = s.strip(" .")  # no leading/trailing dot or space
    s = s[:_MAX_SEGMENT].strip()
    return s or UNKNOWN


def sanitize_filename(name: str) -> str:
    """Sanitize a filename while preserving its extension."""
    p = PurePosixPath(name)
    stem = sanitize_segment(p.stem)
    ext = _ILLEGAL.sub("_", p.suffix)
    return f"{stem}{ext}"


def relative_dir(template: list[str], ctx: AssetContext, album: str | None) -> PurePosixPath:
    """Resolve the template into a sanitized relative directory."""
    parts = [sanitize_segment(_substitute(seg, ctx, album)) for seg in template]
    out = PurePosixPath()
    for p in parts:
        out = out / p
    return out


def album_targets(template: list[str], ctx: AssetContext, fanout: bool) -> list[str | None]:
    """Albums to resolve against (D8).

    Fanout matters only when the template uses {album}: then we emit one target
    per album (fanout on) or just the first (fanout off). Without {album}, a
    single target (all albums collapse to the same path anyway).
    """
    has_album = any("{album}" in seg for seg in template)
    if has_album and ctx.albums:
        return list(ctx.albums) if fanout else [ctx.albums[0]]
    return [ctx.albums[0] if ctx.albums else None]


def final_path(
    base: str, template: list[str], ctx: AssetContext, album: str | None, filename: str
) -> PurePosixPath:
    """Full output path: base / resolved-dir / sanitized-filename."""
    return PurePosixPath(base) / relative_dir(template, ctx, album) / sanitize_filename(filename)


def _short_id(asset_id: str, n: int = 6) -> str:
    return hashlib.sha1(asset_id.encode("utf-8")).hexdigest()[:n]


def disambiguate(
    path: PurePosixPath, asset_id: str, is_taken: Callable[[PurePosixPath], bool]
) -> PurePosixPath:
    """Return a non-colliding path (D7).

    `is_taken(p)` reports whether `p` is already occupied by a *different* asset
    (the caller checks the DB `files` table). Free paths are returned unchanged;
    collisions get an asset-id-derived suffix, then a numeric tail as last resort.
    Never silently overwrites another asset.
    """
    if not is_taken(path):
        return path
    short = _short_id(asset_id)
    candidate = path.with_name(f"{PurePosixPath(path.name).stem}~{short}{path.suffix}")
    if not is_taken(candidate):
        return candidate
    stem = PurePosixPath(path.name).stem
    i = 1
    while True:
        c = path.with_name(f"{stem}~{short}_{i}{path.suffix}")
        if not is_taken(c):
            return c
        i += 1


def with_suffix_name(filename: str, stem_suffix: str, ext: str | None = None) -> str:
    """Build a filename with a stem suffix and/or a different extension.

    e.g. ("IMG_0042.HEIC", "_edited") -> "IMG_0042_edited.HEIC"
         ("IMG_0042.HEIC", "_live", ".MOV") -> "IMG_0042_live.MOV"
    """
    p = PurePosixPath(filename)
    new_ext = ext if ext is not None else p.suffix
    return f"{p.stem}{stem_suffix}{new_ext}"
