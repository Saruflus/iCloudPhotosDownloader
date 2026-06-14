#!/usr/bin/env python3
"""Settings API tests (Lot 2) — fake repo via TestClient + /api/tokens.

Run:  cd backend && PYTHONPATH=. python tests/test_settings_api.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import settings as settings_api  # noqa: E402
from app.core.overrides import load_overrides_sync, validate_override  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeRepo:
    def __init__(self):
        self.store: dict = {}

    async def all(self):
        return dict(self.store)

    async def set(self, key, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


def run() -> None:
    repo = FakeRepo()
    main.app.dependency_overrides[settings_api.get_settings_repo] = lambda: repo

    with TestClient(main.app) as client:
        print("== GET effective settings ==")
        r = client.get("/api/settings")
        check("GET 200", r.status_code == 200)
        s = r.json()
        check("env defaults surfaced", s["download_concurrency"] == 4 and s["max_retries"] == 3)
        check("nothing overridden", s["overridden"] == [])
        check("paths read-only present", s["download_base_path"] == "/downloads")
        check("notify channels reported", s["notify_channels"] == [])
        check("notify flags reported", s["notify_on_failure"] is True and s["notify_on_success"] is False)

        print("== PUT overrides ==")
        r = client.put("/api/settings", json={"download_concurrency": 8, "local_timezone": "Europe/Paris"})
        check("PUT 200", r.status_code == 200)
        s = r.json()
        check("override applied", s["download_concurrency"] == 8)
        check("tz override applied", s["local_timezone"] == "Europe/Paris")
        check("overridden listed", s["overridden"] == ["download_concurrency", "local_timezone"])

        print("== validation ==")
        check("concurrency 0 → 400", client.put("/api/settings", json={"download_concurrency": 0}).status_code == 400)
        check("concurrency 99 → 400", client.put("/api/settings", json={"download_concurrency": 99}).status_code == 400)
        check("bad tz → 400", client.put("/api/settings", json={"local_timezone": "Mars/Olympus"}).status_code == 400)

        print("== reset ==")
        r = client.delete("/api/settings/download_concurrency")
        check("reset 200", r.status_code == 200)
        check("back to env default", r.json()["download_concurrency"] == 4)
        check("reset unknown key → 404", client.delete("/api/settings/nope").status_code == 404)

        print("== /api/tokens ==")
        r = client.get("/api/tokens")
        check("tokens 200", r.status_code == 200)
        ids = {t["id"] for t in r.json()}
        check("core tokens present", {"year", "month", "album", "mediatype"} <= ids)

    main.app.dependency_overrides.clear()

    print("== overrides validators (worker side) ==")
    check("validate ok", validate_override("max_retries", 5) == 5)
    try:
        validate_override("api_shared_secret", "x")
        check("non-whitelisted rejected", False)
    except ValueError:
        check("non-whitelisted rejected", True)

    class Row:
        def __init__(self, key, value):
            self.key = key
            self.value = value

    class FakeSession:
        def query(self, _model):
            class Q:
                @staticmethod
                def all():
                    return [Row("download_concurrency", 8), Row("garbage_key", 1),
                            Row("max_retries", "not-an-int... wait yes it casts")]
            return Q()

    # int("not-an-int...") raises ValueError → row skipped, not fatal
    loaded = load_overrides_sync(FakeSession())
    check("worker loads valid overrides only", loaded == {"download_concurrency": 8})

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
