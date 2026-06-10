#!/usr/bin/env python3
"""EXIF extraction tests (D5).

Synthesizes a JPEG, uses the real HEIC in ~/Downloads if present, and checks
graceful handling of garbage/unknown files + the timezone fallback.

Run:  cd backend && PYTHONPATH=. python tests/test_exif.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.services import exif

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


def make_jpeg(path: Path) -> None:
    from PIL import Image
    import piexif

    img = Image.new("RGB", (8, 8), (120, 30, 30))
    exif_dict = {
        "0th": {piexif.ImageIFD.Make: b"TestMake", piexif.ImageIFD.Model: b"TestCam"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2021:07:04 12:34:56"},
    }
    img.save(path, "jpeg", exif=piexif.dump(exif_dict))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="exif_test_"))

    print("== JPEG (synthesized) ==")
    jpg = tmp / "sample.jpg"
    make_jpeg(jpg)
    r = exif.extract(jpg)
    check("jpeg datetime", r.get("datetime_original") == datetime(2021, 7, 4, 12, 34, 56))
    check("jpeg make", r.get("make") == "TestMake")
    check("jpeg model", r.get("model") == "TestCam")

    print("== HEIC (real, if available) ==")
    heic = Path.home() / "Downloads" / "IMG_2522_original.heic"
    if heic.exists():
        r = exif.extract(heic)
        check("heic make is Apple", r.get("make") == "Apple")
        check("heic datetime parsed", isinstance(r.get("datetime_original"), datetime))
        check("heic model present", bool(r.get("model")))
    else:
        print("  (skip: ~/Downloads/IMG_2522_original.heic not found)")

    print("== graceful failures ==")
    junk = tmp / "junk.jpg"
    junk.write_bytes(b"not really an image")
    check("garbage jpeg → {}", exif.extract(junk) == {})
    unknown = tmp / "note.txt"
    unknown.write_text("hi")
    check("unknown ext → {}", exif.extract(unknown) == {})
    check("missing file → {}", exif.extract(tmp / "nope.heic") == {})

    print("== resolve_capture_local (tz) ==")
    # EXIF present → used as-is
    dt = datetime(2021, 7, 4, 12, 34, 56)
    check("exif wins",
          exif.resolve_capture_local({"datetime_original": dt}, None, "UTC") == dt)
    # Fallback: UTC 02:00 → LA is previous day 18:00 (naive local)
    icloud = datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc)
    local = exif.resolve_capture_local({}, icloud, "America/Los_Angeles")
    check("utc→local fallback", local == datetime(2023, 12, 31, 18, 0))
    check("no exif, no fallback → None", exif.resolve_capture_local({}, None, "UTC") is None)

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
