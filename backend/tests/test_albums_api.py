#!/usr/bin/env python3
"""Albums API tests — TestClient + fake ICloudService + fake async Redis.

Run:  cd backend && PYTHONPATH=. python tests/test_albums_api.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import deps  # noqa: E402
from app.core import redis as redis_mod  # noqa: E402
from app.services.icloud import AssetMetadata, ICloudError  # noqa: E402


class FakeICloud:
    def get_albums(self) -> list[dict]:
        return [
            {"name": "Fuji", "asset_count": 1394},
            {"name": "Library", "asset_count": 22367},
        ]

    def get_assets(self, name, offset, limit) -> list[AssetMetadata]:
        if name == "Nope":
            raise ICloudError("Album not found: Nope")
        return [
            AssetMetadata(
                asset_id="A1",
                filename="IMG_2522.HEIC",
                media_type="HEIC",
                file_size=3397242,
                created_at=datetime(2026, 6, 7, 16, 4, tzinfo=timezone.utc),
                is_live_photo=False,
                has_edited_version=True,
            )
        ]

    def thumbnail_for(self, asset_id) -> bytes | None:
        return b"\xff\xd8_fake_jpeg_bytes_" if asset_id == "A1" else None


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.set_calls = 0

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        self.set_calls += 1


PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


def run() -> None:
    fake = FakeICloud()
    fr = FakeRedis()
    main.app.dependency_overrides[deps.get_service] = lambda: fake
    main.app.dependency_overrides[redis_mod.get_redis] = lambda: fr

    with TestClient(main.app) as client:
        albums = client.get("/api/albums").json()
        check("albums listed", {a["name"] for a in albums} == {"Fuji", "Library"})
        check("album count present", albums[0]["asset_count"] == 1394)

        assets = client.get("/api/albums/Fuji/assets?offset=0&limit=10").json()
        check("assets listed", len(assets) == 1 and assets[0]["asset_id"] == "A1")
        check("has_edited_version surfaced", assets[0]["has_edited_version"] is True)
        check("thumbnail_url built", assets[0]["thumbnail_url"] == "/api/assets/A1/thumbnail")

        check("unknown album → 404", client.get("/api/albums/Nope/assets").status_code == 404)

        r = client.get("/api/assets/A1/thumbnail")
        check("thumbnail 200", r.status_code == 200)
        check("thumbnail is jpeg", r.headers["content-type"] == "image/jpeg")
        check("thumbnail bytes", r.content == b"\xff\xd8_fake_jpeg_bytes_")
        check("thumbnail cached in redis", fr.store.get("thumb:A1") == b"\xff\xd8_fake_jpeg_bytes_")

        # Second request must be served from the Redis cache (no new set call).
        before = fr.set_calls
        r2 = client.get("/api/assets/A1/thumbnail")
        check("2nd thumbnail still 200", r2.status_code == 200)
        check("2nd served from cache (no extra set)", fr.set_calls == before)

        check("unknown asset → 404", client.get("/api/assets/ZZZ/thumbnail").status_code == 404)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
