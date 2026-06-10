#!/usr/bin/env python3
"""End-to-end check of ICloudService — closes the D6 'edited download' risk.

Authenticates from the saved session, lists albums, grabs the newest photo
(item 0 of Recently Added — the one you just edited), and downloads BOTH the
original and edited renditions, reporting sizes. If 'edited' is retrieved and
differs from 'original', edited end-to-end works.

    cd backend
    export ICLOUD_APPLE_ID="merle1sb@gmail.com"
    export ICLOUD_CONFIG_DIR="./icloud-session"
    python verify_icloud.py
"""
from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path

from app.services.icloud import ICloudService


def main() -> None:
    apple_id = os.environ.get("ICLOUD_APPLE_ID") or input("Apple ID: ").strip()
    password = os.environ.get("ICLOUD_PASSWORD") or getpass.getpass("Password: ")
    cookie_dir = os.environ.get("ICLOUD_CONFIG_DIR", "./icloud-session")

    svc = ICloudService(cookie_dir)
    if svc.authenticate(apple_id, password):
        svc.submit_2fa(input("2FA code: ").strip())
    print("status:", svc.get_status())
    print("albums:", len(svc.get_albums()))

    # Newest asset = item 0 of Recently Added (the photo you just edited).
    recent = svc.api.photos._root_library.recently_added()
    photo = next(iter(recent))
    print(f"\nnewest asset: {photo.filename}  id={photo.id[:24]}…")
    print("  has_edited_version:", svc._has_edited(photo))

    tmp = Path(tempfile.mkdtemp(prefix="icloud_verify_"))
    print(f"\ndownloading version='both' → {tmp}")
    files = svc.download_asset(photo, "both", tmp)
    for f in files:
        print(f"  {f.kind:<11} {f.size:>11,} bytes  {f.path.name}")

    kinds = {f.kind for f in files}
    print("\nRESULT:")
    print("  original downloaded:", "original" in kinds)
    print("  edited   downloaded:", "edited" in kinds)
    if "original" in kinds and "edited" in kinds:
        so = next(f.size for f in files if f.kind == "original")
        se = next(f.size for f in files if f.kind == "edited")
        print(f"  sizes differ (orig {so:,} vs edited {se:,}): {so != se}")
        print("  ✓ edited end-to-end retrieval WORKS")
    elif "edited" not in kinds:
        print("  (newest asset has no edit — take+edit a photo first, then re-run)")


if __name__ == "__main__":
    main()
