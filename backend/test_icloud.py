#!/usr/bin/env python3
"""Step-2 go/no-go spike (D12, D6) — targets pyicloud 2.6.x.

Standalone — depends only on `pyicloud`. Must succeed before building the app.

Verifies:
  1. Auth + 2FA, persisting a trusted session to a cookie dir (→ /config later).
  2. Album listing with counts.
  3. CloudKit fields on your NEWEST photos — dumping BOTH the master record
     (originals/derivatives) AND the asset record (where EDITS live), and
     hunting for any edit/adjustment rendition field (the D6 question).
  4. (optional) A thumbnail download.

To force a known-edited photo to the top:
    On your iPhone — take a photo, then Edit it (crop or a filter), tap Done,
    wait ~30s for iCloud to sync, then run this. Item [0] should be that photo.

Usage:
    export ICLOUD_APPLE_ID="you@example.com"
    export ICLOUD_CONFIG_DIR="./icloud-session"
    python test_icloud.py                 # newest-first
    python test_icloud.py --album "Fuji"  # a specific album
    python test_icloud.py --download

ADP (Advanced Data Protection) must be DISABLED on the account.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path

try:
    from pyicloud import PyiCloudService
except ImportError:
    from pyicloud.base import PyiCloudService  # type: ignore

# Field-name patterns that would indicate an edited/adjusted rendition.
EDIT_HINT = re.compile(r"jpegfull|adjust|mutation|encodedscene|edited|fullsizejpeg", re.I)


def hr(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * max(4, 70 - len(title))}")


def authenticate(apple_id: str, password: str, cookie_dir: Path) -> PyiCloudService:
    cookie_dir.mkdir(parents=True, exist_ok=True)
    api = PyiCloudService(apple_id, password, cookie_directory=str(cookie_dir))
    if getattr(api, "requires_2fa", False):
        print("Two-factor auth required.")
        code = input("  Enter the 6-digit code: ").strip()
        if not api.validate_2fa_code(code):
            print("  ✗ 2FA validation failed."); sys.exit(2)
        if not getattr(api, "is_trusted_session", False):
            api.trust_session()
        print("  ✓ 2FA OK, session trusted.")
    elif getattr(api, "requires_2sa", False):
        print("2SA required.")
        devices = api.trusted_devices
        for i, d in enumerate(devices):
            print(f"  [{i}] {d.get('deviceName', d.get('phoneNumber', d))}")
        dev = devices[int(input("  Device #: ") or 0)]
        api.send_verification_code(dev)
        if not api.validate_verification_code(dev, input("  Code: ").strip()):
            print("  ✗ 2SA validation failed"); sys.exit(2)
        print("  ✓ 2SA OK.")
    else:
        print("  ✓ Authenticated from existing trusted session (no 2FA prompt).")
    return api


def rec_fields(photo, which: str) -> list[str]:
    """Field names from the master ('master') or asset ('asset') CKRecord."""
    rec = getattr(photo, "_master_record" if which == "master" else "_asset_record", None)
    if rec is None:
        return []
    try:
        return sorted(rec.fields.keys())
    except Exception as e:
        return [f"<error: {e!r}>"]


def edit_hits(photo) -> list[str]:
    both = set(rec_fields(photo, "master")) | set(rec_fields(photo, "asset"))
    return sorted(f for f in both if EDIT_HINT.search(f))


def describe(photo) -> dict:
    return {
        "filename": getattr(photo, "filename", "?"),
        "item_type": getattr(photo, "item_type", "?"),
        "created": str(getattr(photo, "created", "?"))[:19],
        "versions": sorted((getattr(photo, "versions", {}) or {}).keys()),
        "edit_fields": edit_hits(photo),
        "live": bool(getattr(photo, "is_live_photo", False)),
    }


def pick_source(api, album_name: str | None):
    albums = api.photos.albums
    if album_name:
        a = albums.find(album_name)
        if a is not None:
            return a, album_name
        print(f"  ✗ album '{album_name}' not found; using Recently Added")
    return api.photos._root_library.recently_added(), "Recently Added (newest-first)"


def dump_records(photo, tag: str) -> None:
    print(f"\n  ══ {tag}: {getattr(photo,'filename','?')} ══")
    for which in ("master", "asset"):
        keys = rec_fields(photo, which)
        print(f"    [{which} record — {len(keys)} fields]")
        for k in keys:
            mark = "  <-- EDIT?" if EDIT_HINT.search(k) else ""
            print(f"      {k}{mark}")


def main() -> None:
    ap = argparse.ArgumentParser(description="iCloud connection spike (step-2 gate)")
    ap.add_argument("--album", default=None, help="album name (default: Recently Added)")
    ap.add_argument("--sample", type=int, default=20, help="assets to print in detail")
    ap.add_argument("--scan", type=int, default=800, help="assets to examine for edits")
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()

    apple_id = os.environ.get("ICLOUD_APPLE_ID") or input("Apple ID: ").strip()
    password = os.environ.get("ICLOUD_PASSWORD") or getpass.getpass("Password: ")
    cookie_dir = Path(os.environ.get("ICLOUD_CONFIG_DIR", "./icloud-session")).expanduser()

    hr("AUTH")
    print(f"Apple ID: {apple_id}   cookie dir: {cookie_dir}")
    api = authenticate(apple_id, password, cookie_dir)

    hr("ALBUMS")
    album_objs = list(api.photos.albums)
    print(f"{len(album_objs)} albums (counts omitted — already captured)")

    source, label = pick_source(api, args.album)
    hr(f"SCAN: {label} (detail first {args.sample}, examine up to {args.scan})")

    live = examined = 0
    all_edit_fields: set[str] = set()
    edited_assets: list = []
    first_photo = None
    first_three = []
    for i, photo in enumerate(source):
        if i >= args.scan:
            break
        examined = i + 1
        if first_photo is None:
            first_photo = photo
        if len(first_three) < 3:
            first_three.append(photo)
        d = describe(photo)
        live += d["live"]
        if d["edit_fields"]:
            all_edit_fields.update(d["edit_fields"])
            if len(edited_assets) < 3:
                edited_assets.append(photo)
        if i < args.sample:
            flags = "".join(c for c, on in
                            (("E", bool(d["edit_fields"])), ("L", d["live"])) if on) or "-"
            print(f"  [{i:>3}] {flags:<3} {d['created']}  {d['filename']:<28} versions={d['versions']}")

    hr("RENDITION SUMMARY (D6)")
    print(f"  examined:                 {examined}")
    print(f"  Live Photos:              {live}")
    print(f"  edit/adjust field names seen across examined assets:")
    if all_edit_fields:
        for f in sorted(all_edit_fields):
            print(f"      • {f}")
    else:
        print("      (none — but note fullSizeJPEGSource on master is common and non-conclusive)")

    hr("FULL RECORD DUMP — first 3 newest assets (master + asset)")
    for p in first_three:
        dump_records(p, "asset")

    if edited_assets:
        hr("ASSETS WITH EDIT-LIKE FIELDS — full dump")
        for p in edited_assets:
            print(json.dumps(describe(p), indent=2, default=str))
            dump_records(p, "edited?")

    if args.download and first_photo is not None:
        hr("DOWNLOAD TEST (thumb)")
        try:
            data = first_photo.download("thumb")
            raw = getattr(data, "raw", None)
            blob = raw.read() if raw is not None else data
            out = cookie_dir / f"_spike_thumb_{first_photo.filename}"
            out.write_bytes(blob)
            print(f"  ✓ wrote {len(blob)} bytes → {out}")
        except Exception as e:
            print(f"  ✗ download failed: {e!r}")

    hr("RESULT")
    print("  ✓ Auth + albums + dual-record field access succeeded.")
    print("  Paste the RENDITION SUMMARY + the FULL RECORD DUMP (esp. the asset record).")
    print("  If you took+edited a photo first, item [0]'s asset record reveals the edit field.")


if __name__ == "__main__":
    main()
