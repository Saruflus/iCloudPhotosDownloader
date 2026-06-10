#!/usr/bin/env python3
"""Path resolver tests (D5/D7/D8) — pure logic, no env needed.

Run:  cd backend && PYTHONPATH=. python tests/test_paths.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import PurePosixPath

from app.core import paths as P
from app.core.paths import AssetContext

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


def run() -> None:
    ctx = AssetContext(
        filename="IMG_0042.HEIC",
        capture_dt=datetime(2024, 6, 15, 9, 30),
        albums=["Holidays", "Family"],
        persons=["Alice"],
        media_type="HEIC",
        make="Apple",
        model="iPhone 15 Pro",
    )

    print("== classify_media ==")
    check("raw", P.classify_media("cr2") == "RAW")
    check("video", P.classify_media(".MOV") == "Video")
    check("heic", P.classify_media("heic") == "HEIC")
    check("jpeg", P.classify_media("JPG") == "JPEG")
    check("png passthrough", P.classify_media("png") == "PNG")
    check("empty", P.classify_media("") == "Unknown")

    print("== relative_dir / tokens ==")
    check("basic y/m/album",
          P.relative_dir(["{year}", "{month}", "{album}"], ctx, "Holidays")
          == PurePosixPath("2024/06/Holidays"))
    check("plain string segment",
          P.relative_dir(["Photos", "{year}"], ctx, None) == PurePosixPath("Photos/2024"))
    check("album fallback to first when None",
          P.relative_dir(["{album}"], ctx, None) == PurePosixPath("Holidays"))
    check("explicit album",
          P.relative_dir(["{album}"], ctx, "Family") == PurePosixPath("Family"))
    check("person / make / model / mediatype / filename",
          P.relative_dir(["{person}", "{make}", "{model}", "{mediatype}", "{filename}"], ctx, None)
          == PurePosixPath("Alice/Apple/iPhone 15 Pro/HEIC/IMG_0042"))
    check("mixed tokens in one segment",
          P.relative_dir(["{year}-{month}"], ctx, None) == PurePosixPath("2024-06"))

    print("== date fallback ==")
    undated = AssetContext(filename="x.jpg", capture_dt=None)
    check("undated year/month",
          P.relative_dir(["{year}", "{month}"], undated, None) == PurePosixPath("Undated/Undated"))

    print("== sanitization / traversal ==")
    trav = P.sanitize_segment("../../etc")
    check("no traversal", ".." not in trav and "/" not in trav)
    check("illegal chars replaced", P.sanitize_segment('a<b>c:d') == "a_b_c_d")
    check("empty → Unknown", P.sanitize_segment("   ") == "Unknown")
    check("trailing dot/space trimmed", P.sanitize_segment(" name. ") == "name")
    check("length capped", len(P.sanitize_segment("x" * 500)) <= 120)
    check("filename keeps ext", P.sanitize_filename("IMG_0042.HEIC") == "IMG_0042.HEIC")
    check("filename sanitizes stem",
          P.sanitize_filename('a<b>.jpg') == "a_b_.jpg")  # '<' and '>' both → '_'

    print("== album_targets (fanout, D8) ==")
    tmpl = ["{year}", "{album}"]
    check("fanout → per album", P.album_targets(tmpl, ctx, fanout=True) == ["Holidays", "Family"])
    check("no fanout → first only", P.album_targets(tmpl, ctx, fanout=False) == ["Holidays"])
    check("no {album} token → single", P.album_targets(["{year}"], ctx, fanout=True) == ["Holidays"])
    noalb = AssetContext(filename="x.jpg", albums=[])
    check("no albums → [None]", P.album_targets(tmpl, noalb, fanout=True) == [None])

    print("== final_path ==")
    check("full path",
          P.final_path("/downloads", ["{year}", "{album}"], ctx, "Holidays", "IMG_0042.HEIC")
          == PurePosixPath("/downloads/2024/Holidays/IMG_0042.HEIC"))

    print("== disambiguate (collision, D7) ==")
    p = PurePosixPath("/d/IMG.HEIC")
    taken: set[PurePosixPath] = set()
    check("free path unchanged", P.disambiguate(p, "asset-1", lambda x: x in taken) == p)
    taken.add(p)
    d1 = P.disambiguate(p, "asset-1", lambda x: x in taken)
    check("collision → suffixed", d1 != p and "~" in d1.name and d1.suffix == ".HEIC")
    taken.add(d1)
    d2 = P.disambiguate(p, "asset-1", lambda x: x in taken)
    check("second collision → numeric tail", d2 not in (p, d1) and d2.suffix == ".HEIC")
    check("different assets → different suffix",
          P.disambiguate(p, "asset-2", lambda x: x in {p}).name
          != P.disambiguate(p, "asset-1", lambda x: x in {p}).name)

    print("== with_suffix_name ==")
    check("edited suffix", P.with_suffix_name("IMG_0042.HEIC", "_edited") == "IMG_0042_edited.HEIC")
    check("suffix + ext override",
          P.with_suffix_name("IMG_0042.HEIC", "_live", ".MOV") == "IMG_0042_live.MOV")

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
