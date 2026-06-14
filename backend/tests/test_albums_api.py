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

import asyncio  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import albums as albums_api  # noqa: E402
from app.api import deps  # noqa: E402
from app.core import redis as redis_mod  # noqa: E402
from app.services.icloud import AssetMetadata, ICloudError  # noqa: E402


class FakeThumbCache:
    """In-memory stand-in for the disk thumb cache."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get(self, asset_id):
        return self.store.get(asset_id)

    def put(self, asset_id, data):
        self.store[asset_id] = data


class FakeICloud:
    def get_albums(self, with_counts: bool = True) -> list[dict]:
        return [
            {"name": "Fuji", "asset_count": 1394 if with_counts else None, "shared": False},
            {"name": "Library", "asset_count": 22367 if with_counts else None, "shared": False},
            {"name": "Famille", "asset_count": 52 if with_counts else None, "shared": True},
        ]

    def get_album_count(self, name: str) -> int | None:
        if name == "Nope":
            raise ICloudError("Album not found: Nope")
        return {"Fuji": 1394, "Library": 22367}.get(name)

    def get_assets(self, name, offset, limit) -> list[AssetMetadata]:
        if name == "Nope":
            raise ICloudError("Album not found: Nope")
        return [
            AssetMetadata(
                asset_id="A1",
                filename="IMG_2522.HEIC",
                media_type="HEIC",
                media_category="HEIC",
                file_size=3397242,
                created_at=datetime(2026, 6, 7, 16, 4, tzinfo=timezone.utc),
                is_live_photo=False,
                has_edited_version=True,
                has_raw_version=True,
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
    fc = FakeThumbCache()
    scheduled: list = []
    main.app.dependency_overrides[deps.get_service] = lambda: fake
    main.app.dependency_overrides[redis_mod.get_redis] = lambda: fr
    main.app.dependency_overrides[albums_api.get_thumb_cache] = lambda: fc
    main.app.dependency_overrides[albums_api.get_prefetch_scheduler] = (
        lambda: lambda coro: scheduled.append(coro)
    )

    with TestClient(main.app) as client:
        albums = client.get("/api/albums").json()
        check("albums listed", {a["name"] for a in albums} == {"Fuji", "Library", "Famille"})
        check("counts lazy by default", albums[0]["asset_count"] is None)
        check("shared flag surfaced",
              [a["shared"] for a in albums] == [False, False, True])

        eager = client.get("/api/albums?with_counts=true").json()
        check("with_counts=true returns counts", eager[0]["asset_count"] == 1394)

        cnt = client.get("/api/albums/Fuji/count").json()
        check("count endpoint", cnt == {"name": "Fuji", "asset_count": 1394})
        check("count unknown album → 404", client.get("/api/albums/Nope/count").status_code == 404)

        assets = client.get("/api/albums/Fuji/assets?offset=0&limit=10").json()
        check("assets listed", len(assets) == 1 and assets[0]["asset_id"] == "A1")
        check("has_edited_version surfaced", assets[0]["has_edited_version"] is True)
        check("has_raw_version surfaced", assets[0]["has_raw_version"] is True)
        check("media_category surfaced", assets[0]["media_category"] == "HEIC")
        check("thumbnail_url built", assets[0]["thumbnail_url"] == "/api/assets/A1/thumbnail")

        check("unknown album → 404", client.get("/api/albums/Nope/assets").status_code == 404)

        r = client.get("/api/assets/A1/thumbnail")
        check("thumbnail 200", r.status_code == 200)
        check("thumbnail is jpeg", r.headers["content-type"] == "image/jpeg")
        check("thumbnail cache headers", "immutable" in r.headers.get("cache-control", ""))
        check("thumbnail bytes", r.content == b"\xff\xd8_fake_jpeg_bytes_")
        check("thumbnail cached in redis", fr.store.get("thumb:A1") == b"\xff\xd8_fake_jpeg_bytes_")

        # Second request must be served from the Redis cache (no new set call).
        before = fr.set_calls
        r2 = client.get("/api/assets/A1/thumbnail")
        check("2nd thumbnail still 200", r2.status_code == 200)
        check("2nd served from cache (no extra set)", fr.set_calls == before)

        check("unknown asset → 404", client.get("/api/assets/ZZZ/thumbnail").status_code == 404)

        print("== disk thumb cache (Lot 3) ==")
        check("thumb also written to disk cache", fc.store.get("A1") == b"\xff\xd8_fake_jpeg_bytes_")
        # Redis cold + disk warm → served from disk and re-warmed into Redis.
        fr.store.clear()
        r3 = client.get("/api/assets/A1/thumbnail")
        check("served from disk when redis cold", r3.status_code == 200 and r3.content == b"\xff\xd8_fake_jpeg_bytes_")
        check("redis re-warmed from disk", fr.store.get("thumb:A1") == b"\xff\xd8_fake_jpeg_bytes_")

        print("== next-page prefetch (Lot 3) ==")
        # A full page (limit == returned count) schedules a prefetch of the next page.
        scheduled.clear()
        client.get("/api/albums/Fuji/assets?offset=0&limit=1")
        check("prefetch scheduled on full page", len(scheduled) == 1)
        # A short page (fewer assets than limit) must NOT schedule.
        client.get("/api/albums/Fuji/assets?offset=0&limit=10")
        check("no prefetch on short page", len(scheduled) == 1)
        # prefetch=false opts out.
        client.get("/api/albums/Fuji/assets?offset=0&limit=1&prefetch=false")
        check("prefetch can be disabled", len(scheduled) == 1)
        for coro in scheduled:
            coro.close()

        # Run the prefetch coroutine itself against fresh fakes.
        fr2, fc2 = FakeRedis(), FakeThumbCache()
        asyncio.run(albums_api.prefetch_page(fake, fr2, fc2, "Fuji", 1, 10))
        check("prefetch warms redis", fr2.store.get("thumb:A1") == b"\xff\xd8_fake_jpeg_bytes_")
        check("prefetch warms disk", fc2.store.get("A1") == b"\xff\xd8_fake_jpeg_bytes_")
        # Idempotent: already-warm page does no extra Redis writes.
        before = fr2.set_calls
        asyncio.run(albums_api.prefetch_page(fake, fr2, fc2, "Fuji", 1, 10))
        check("prefetch skips warm thumbs", fr2.set_calls == before)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
