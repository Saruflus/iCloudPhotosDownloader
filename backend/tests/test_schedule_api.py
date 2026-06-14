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
        self.rows: dict[int, dict] = {}
        self._next = 1

    # -- single-schedule compat surface (operates on the first row)
    @property
    def row(self):
        ids = sorted(self.rows)
        return self.rows[ids[0]] if ids else None

    async def get(self):
        return dict(self.row) if self.row else None

    async def upsert(self, *, cron, job_config, enabled, next_run):
        if self.row is None:
            return await self.create(cron=cron, job_config=job_config,
                                     enabled=enabled, next_run=next_run)
        self.row.update(cron_expression=cron, job_config=job_config,
                        enabled=enabled, next_run_at=next_run)
        return dict(self.row)

    async def set_enabled(self, enabled, next_run):
        if not self.row:
            return None
        self.row["enabled"] = enabled
        self.row["next_run_at"] = next_run
        return dict(self.row)

    # -- multi-schedule surface (Lot 4)
    async def list_all(self):
        return [dict(self.rows[i]) for i in sorted(self.rows)]

    async def create(self, *, cron, job_config, enabled, next_run):
        sid = self._next
        self._next += 1
        self.rows[sid] = {"id": sid, "cron_expression": cron, "job_config": job_config,
                          "enabled": enabled, "last_run_at": None, "next_run_at": next_run}
        return dict(self.rows[sid])

    async def update(self, sid, *, cron, job_config, enabled, next_run):
        if sid not in self.rows:
            return None
        self.rows[sid].update(cron_expression=cron, job_config=job_config,
                              enabled=enabled, next_run_at=next_run)
        return dict(self.rows[sid])

    async def delete(self, sid):
        return self.rows.pop(sid, None) is not None

    async def set_enabled_by_id(self, sid, enabled, next_run):
        if sid not in self.rows:
            return None
        self.rows[sid]["enabled"] = enabled
        self.rows[sid]["next_run_at"] = next_run
        return dict(self.rows[sid])


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

        print("== multi-schedule CRUD (Lot 4) ==")
        r = client.post("/api/schedules", json={
            "cron_expression": "0 */6 * * *",
            "job_config": {"selected_albums": ["Famille"]},
            "enabled": True,
        })
        check("create 201", r.status_code == 201)
        second = r.json()
        check("new id assigned", second["id"] == 2)

        lst = client.get("/api/schedules").json()
        check("list shows both", [s["id"] for s in lst] == [1, 2])

        r = client.put(f"/api/schedules/{second['id']}", json={
            "cron_expression": "30 4 * * *",
            "job_config": {"selected_albums": ["Famille", "Fuji"]},
            "enabled": True,
        })
        check("update 200", r.status_code == 200 and r.json()["cron_expression"] == "30 4 * * *")
        check("update bad cron → 400", client.put(f"/api/schedules/{second['id']}", json={
            "cron_expression": "nope", "job_config": {}, "enabled": True}).status_code == 400)
        check("update unknown → 404", client.put("/api/schedules/99", json={
            "cron_expression": "0 2 * * *", "job_config": {}, "enabled": True}).status_code == 404)

        t = client.post(f"/api/schedules/{second['id']}/toggle", json={"enabled": False})
        check("toggle by id", t.status_code == 200 and t.json()["enabled"] is False)
        check("toggle unknown → 404", client.post("/api/schedules/99/toggle",
                                                  json={"enabled": True}).status_code == 404)

        check("delete", client.delete(f"/api/schedules/{second['id']}").json() == {"deleted": True})
        check("delete unknown → 404", client.delete("/api/schedules/99").status_code == 404)
        check("list after delete", [s["id"] for s in client.get("/api/schedules").json()] == [1])
        check("mutations notified", notifier.count >= 6)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
