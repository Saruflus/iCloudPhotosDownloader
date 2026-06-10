#!/usr/bin/env python3
"""Schedule API + cron helper tests — fakes via TestClient.

Run:  cd backend && PYTHONPATH=. python tests/test_schedule_api.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import schedule as sched_api  # noqa: E402
from app.services.scheduler import build_job_spec, next_run_after, valid_cron  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeRepo:
    def __init__(self):
        self.row = None

    async def get(self):
        return dict(self.row) if self.row else None

    async def upsert(self, *, cron, job_config, enabled, next_run):
        self.row = {"id": 1, "cron_expression": cron, "job_config": job_config,
                    "enabled": enabled, "last_run_at": None, "next_run_at": next_run}
        return dict(self.row)

    async def set_enabled(self, enabled, next_run):
        if not self.row:
            return None
        self.row["enabled"] = enabled
        self.row["next_run_at"] = next_run
        return dict(self.row)


class FakeNotifier:
    def __init__(self):
        self.count = 0

    async def notify(self):
        self.count += 1


def run() -> None:
    print("== cron helpers ==")
    check("valid cron", valid_cron("0 2 * * *") is True)
    check("invalid cron", valid_cron("not a cron") is False)
    nxt = next_run_after("0 2 * * *", base=datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc))
    check("next run is 02:00 next day", nxt == datetime(2024, 6, 16, 2, 0, tzinfo=timezone.utc))
    spec = build_job_spec({"selected_albums": ["A"], "download_version": "both", "junk": 1})
    check("build_job_spec filters keys", spec == {"selected_albums": ["A"], "download_version": "both"})

    repo, notifier = FakeRepo(), FakeNotifier()
    main.app.dependency_overrides[sched_api.get_schedule_repo] = lambda: repo
    main.app.dependency_overrides[sched_api.get_notifier] = lambda: notifier

    with TestClient(main.app) as client:
        print("== API ==")
        check("get when none → null", client.get("/api/schedule").json() is None)
        check("toggle when none → 404", client.post("/api/schedule/toggle", json={"enabled": True}).status_code == 404)

        bad = client.put("/api/schedule", json={"cron_expression": "nope", "job_config": {}})
        check("invalid cron → 400", bad.status_code == 400)

        r = client.put("/api/schedule", json={
            "cron_expression": "0 2 * * *",
            "job_config": {"selected_albums": ["Fuji"], "folder_structure": ["{year}"]},
            "enabled": True,
        })
        check("put 200", r.status_code == 200)
        sched = r.json()
        check("enabled + next_run set", sched["enabled"] is True and sched["next_run_at"] is not None)
        check("notified on put", notifier.count == 1)

        got = client.get("/api/schedule").json()
        check("get returns saved", got["cron_expression"] == "0 2 * * *"
              and got["job_config"]["selected_albums"] == ["Fuji"])

        off = client.post("/api/schedule/toggle", json={"enabled": False}).json()
        check("toggle off", off["enabled"] is False and off["next_run_at"] is None)
        check("notified on toggle", notifier.count == 2)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
