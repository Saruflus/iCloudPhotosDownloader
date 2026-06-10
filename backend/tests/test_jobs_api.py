#!/usr/bin/env python3
"""Jobs API tests — fake async repo + fake queue via TestClient.

Run:  cd backend && PYTHONPATH=. python tests/test_jobs_api.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import jobs as jobs_api  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeRepo:
    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self._next = 1

    async def create(self, spec):
        jid = self._next
        self._next += 1
        job = {"id": jid, "status": "pending", "celery_task_id": None}
        for k in jobs_api.CONFIG_KEYS:
            if spec.get(k) is not None:
                job[k] = spec[k]
        self.jobs[jid] = job
        return dict(job)

    async def set_task_id(self, jid, tid):
        self.jobs[jid]["celery_task_id"] = tid

    async def list_jobs(self):
        return [dict(j) for j in sorted(self.jobs.values(), key=lambda x: -x["id"])]

    async def get_job(self, jid):
        return dict(self.jobs[jid]) if jid in self.jobs else None

    async def request_cancel(self, jid):
        if jid not in self.jobs:
            return None
        self.jobs[jid]["cancel_requested"] = True
        self.jobs[jid]["status"] = "cancelled"
        return self.jobs[jid].get("celery_task_id")

    async def clone_config(self, jid):
        if jid not in self.jobs:
            return None
        j = self.jobs[jid]
        return {k: j.get(k) for k in jobs_api.CONFIG_KEYS}


class FakeQueue:
    def __init__(self):
        self.enqueued = []
        self.revoked = []

    def enqueue(self, job_id):
        self.enqueued.append(job_id)
        return f"task-{job_id}"

    def revoke(self, task_id):
        self.revoked.append(task_id)


def run() -> None:
    repo, queue = FakeRepo(), FakeQueue()
    main.app.dependency_overrides[jobs_api.get_jobs_repo] = lambda: repo
    main.app.dependency_overrides[jobs_api.get_queue] = lambda: queue

    with TestClient(main.app) as client:
        print("== create + enqueue ==")
        body = {"selected_albums": ["Fuji"], "folder_structure": ["{year}", "{album}"],
                "download_version": "edited"}
        r = client.post("/api/jobs", json=body)
        check("201 created", r.status_code == 201)
        job = r.json()
        check("status pending", job["status"] == "pending")
        check("task id set", job["celery_task_id"] == "task-1")
        check("enqueued", queue.enqueued == [1])
        check("config persisted", job["selected_albums"] == ["Fuji"]
              and job["folder_structure"] == ["{year}", "{album}"])

        print("== validation ==")
        bad = client.post("/api/jobs", json={"folder_structure": ["{year}"]})
        check("no album/asset → 400", bad.status_code == 400)

        print("== list / detail ==")
        client.post("/api/jobs", json={"selected_asset_ids": ["a1"], "folder_structure": []})
        lst = client.get("/api/jobs").json()
        check("list newest-first", [j["id"] for j in lst] == [2, 1])
        check("detail ok", client.get("/api/jobs/1").json()["id"] == 1)
        check("detail 404", client.get("/api/jobs/999").status_code == 404)

        print("== cancel ==")
        r = client.delete("/api/jobs/1")
        check("cancel 200", r.status_code == 200 and r.json() == {"cancelled": True})
        check("status cancelled", repo.jobs[1]["status"] == "cancelled")
        check("cancel flag set", repo.jobs[1]["cancel_requested"] is True)
        check("task revoked", queue.revoked == ["task-1"])
        check("cancel unknown → 404", client.delete("/api/jobs/999").status_code == 404)

        print("== retry-failed (clone) ==")
        r = client.post("/api/jobs/1/retry-failed")
        check("retry 201", r.status_code == 201)
        new = r.json()
        check("new job id", new["id"] == 3)
        check("clone keeps scope", new["selected_albums"] == ["Fuji"])
        check("clone enqueued", 3 in queue.enqueued)
        check("retry unknown → 404", client.post("/api/jobs/999/retry-failed").status_code == 404)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
