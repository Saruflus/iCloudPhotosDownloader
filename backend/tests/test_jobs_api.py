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

    async def failed_asset_ids(self, jid):
        return list(self.failed_ids.get(jid, []))

    failed_ids: dict[int, list[str]] = {}


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

        print("== precise retry via job link (Lot 4) ==")
        repo.failed_ids[1] = ["bad1", "bad2"]
        r = client.post("/api/jobs/1/retry-failed")
        check("precise retry narrows to failed assets",
              r.json()["selected_asset_ids"] == ["bad1", "bad2"])
        check("precise retry keeps album scope", r.json()["selected_albums"] == ["Fuji"])
        repo.failed_ids.clear()

        print("== date-range passthrough (Lot 2) ==")
        r = client.post("/api/jobs", json={
            "selected_albums": ["Fuji"], "folder_structure": ["{album}"],
            "date_from": "2024-01-01T00:00:00Z", "date_to": "2024-12-31T23:59:59Z",
        })
        check("dated job created", r.status_code == 201)
        check("date_from persisted", r.json()["date_from"] is not None)
        check("date_to persisted", r.json()["date_to"] is not None)

        print("== dry-run preview (Lot 2) ==")

        class FakePhoto:
            def __init__(self, id, filename, created=None):
                self.id = id
                self.filename = filename
                self.item_type = "image"
                self.created = created

        class FakeService:
            def iter_album(self, name):
                return iter([
                    FakePhoto("a", "a.jpg"), FakePhoto("b", "b.CR2"),
                    FakePhoto("c", "c.jpg"),
                ])

        class FakeLookup:
            async def completed_among(self, ids):
                return 1  # pretend one of the matches is already downloaded

        from app.api import deps as deps_mod
        main.app.dependency_overrides[deps_mod.get_service] = lambda: FakeService()
        main.app.dependency_overrides[jobs_api.get_completed_lookup] = lambda: FakeLookup()

        r = client.post("/api/jobs/preview", json={
            "selected_albums": ["Fuji"], "folder_structure": ["{album}"],
            "include_raw": False,
        })
        check("preview 200", r.status_code == 200)
        p = r.json()
        check("preview listed 3", p["listed"] == 3)
        check("preview matching excludes RAW", p["matching"] == 2)
        check("preview completed counted", p["already_completed"] == 1)
        check("preview to_download", p["to_download"] == 1)

        r = client.post("/api/jobs/preview", json={
            "selected_albums": ["Fuji"], "folder_structure": ["{album}"],
            "include_raw": False, "force_redownload": True,
        })
        check("force ignores completed", r.json()["to_download"] == 2)
        check("preview empty scope → 400",
              client.post("/api/jobs/preview", json={"folder_structure": []}).status_code == 400)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
