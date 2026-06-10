#!/usr/bin/env python3
"""JobRunner tests (D3/D4/D13/D16) — fakes for lock/store/downloader/iCloud.

Run:  cd backend && PYTHONPATH=. python tests/test_job_runner.py
"""
from __future__ import annotations

import sys
import threading
from collections import Counter
from contextlib import contextmanager

os_env = __import__("os").environ
os_env.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.core.locks import LockHeld  # noqa: E402
from app.workers.tasks import JobRecord, JobRunner, Outcome  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakePhoto:
    def __init__(self, id, filename, item_type="image"):
        self.id = id
        self.filename = filename
        self.item_type = item_type
        self.is_live_photo = False


class FakeICloud:
    def __init__(self, by_album):
        self.by_album = by_album

    def iter_album(self, name):
        return iter(self.by_album.get(name, []))


class FakeJobStore:
    def __init__(self, record, cancel=False):
        self.record = record
        self.statuses = []
        self.total = None
        self.counts = None
        self.cancel = cancel

    def get(self, job_id):
        return self.record if self.record and self.record.id == job_id else None

    def set_status(self, job_id, status):
        self.statuses.append(status)

    def set_total(self, job_id, total):
        self.total = total

    def update_counts(self, job_id, *, downloaded, skipped, failed):
        self.counts = (downloaded, skipped, failed)

    def is_cancel_requested(self, job_id):
        return self.cancel


class FakeLock:
    def __init__(self, held=False):
        self.held = held
        self.released = False

    @contextmanager
    def hold(self, owner=None):
        if self.held:
            raise LockHeld("another-worker")
        try:
            yield owner or "me"
        finally:
            self.released = True


class FakeDownloader:
    def __init__(self, plan=None):
        self.plan = plan or {}
        self.calls = Counter()
        self.seen_albums = {}
        self._lock = threading.Lock()

    def download_asset(self, photo, albums, spec, job_id):
        with self._lock:
            self.calls[photo.id] += 1
            attempt = self.calls[photo.id]
            self.seen_albums[photo.id] = list(albums)
        beh = self.plan.get(photo.id, Outcome.downloaded)
        if isinstance(beh, list):
            return beh[min(attempt - 1, len(beh) - 1)]
        return beh


class FakePublisher:
    def __init__(self):
        self.events = []

    def publish(self, job_id, event):
        self.events.append(event)


def make_runner(store, icloud, dl, pub=None, **kw):
    pub = pub or FakePublisher()
    runner = JobRunner(
        job_store=store, lock=FakeLock(), downloader_factory=lambda: (dl, lambda: None),
        icloud=icloud, publisher=pub, sleep=lambda s: None, **kw,
    )
    return runner, pub


def run() -> None:
    print("== happy path ==")
    rec = JobRecord(id=1, selected_albums=["A"], folder_structure=["{album}"])
    photos = [FakePhoto(f"p{i}", f"IMG_{i}.jpg") for i in range(3)]
    store = FakeJobStore(rec)
    dl = FakeDownloader()
    runner, pub = make_runner(store, FakeICloud({"A": photos}), dl)
    status = runner.run(1)
    check("status completed", status == "completed")
    check("running then completed", store.statuses == ["running", "completed"])
    check("total set to 3", store.total == 3)
    check("3 downloaded", store.counts == (3, 0, 0))
    check("done event published", any(e.get("type") == "done" for e in pub.events))
    check("progress published", any(e.get("type") == "progress" for e in pub.events))

    print("== filters (video off, raw off) ==")
    rec = JobRecord(id=2, selected_albums=["A"], folder_structure=["{album}"],
                    include_video=False, include_raw=False)
    photos = [FakePhoto("img", "a.jpg"), FakePhoto("vid", "b.mov", item_type="movie"),
              FakePhoto("raw", "c.CR2")]
    store = FakeJobStore(rec)
    dl = FakeDownloader()
    runner, _ = make_runner(store, FakeICloud({"A": photos}), dl)
    runner.run(2)
    check("only the jpeg processed", set(dl.calls) == {"img"})

    print("== fanout membership (asset in A and B) ==")
    rec = JobRecord(id=3, selected_albums=["A", "B"], folder_structure=["{album}"])
    x = FakePhoto("x", "x.jpg")
    store = FakeJobStore(rec)
    dl = FakeDownloader()
    runner, _ = make_runner(store, FakeICloud({"A": [x], "B": [x]}), dl)
    runner.run(3)
    check("downloaded once (deduped)", dl.calls["x"] == 1)
    check("membership has both albums", dl.seen_albums["x"] == ["A", "B"])

    print("== selected_asset_ids filter ==")
    rec = JobRecord(id=4, selected_albums=["A"], selected_asset_ids=["keep"],
                    folder_structure=["{album}"])
    photos = [FakePhoto("keep", "k.jpg"), FakePhoto("drop", "d.jpg")]
    store = FakeJobStore(rec)
    dl = FakeDownloader()
    runner, _ = make_runner(store, FakeICloud({"A": photos}), dl)
    runner.run(4)
    check("only selected id processed", set(dl.calls) == {"keep"})

    print("== retry then succeed (D16) ==")
    rec = JobRecord(id=5, selected_albums=["A"], folder_structure=["{album}"])
    store = FakeJobStore(rec)
    dl = FakeDownloader(plan={"flaky": [Outcome.failed, Outcome.failed, Outcome.downloaded]})
    runner, _ = make_runner(store, FakeICloud({"A": [FakePhoto("flaky", "f.jpg")]}), dl, max_retries=2)
    status = runner.run(5)
    check("retried to success", dl.calls["flaky"] == 3 and status == "completed")
    check("counted as downloaded", store.counts == (1, 0, 0))

    print("== all failed → status failed ==")
    rec = JobRecord(id=6, selected_albums=["A"], folder_structure=["{album}"])
    store = FakeJobStore(rec)
    dl = FakeDownloader(plan={"bad": Outcome.failed})
    runner, _ = make_runner(store, FakeICloud({"A": [FakePhoto("bad", "b.jpg")]}), dl, max_retries=0)
    check("status failed", runner.run(6) == "failed")
    check("counts 0/0/1", store.counts == (0, 0, 1))

    print("== mixed skip+fail → completed ==")
    rec = JobRecord(id=7, selected_albums=["A"], folder_structure=["{album}"])
    store = FakeJobStore(rec)
    dl = FakeDownloader(plan={"s": Outcome.skipped, "f": Outcome.failed})
    runner, _ = make_runner(store, FakeICloud({"A": [FakePhoto("s", "s.jpg"), FakePhoto("f", "f.jpg")]}),
                            dl, max_retries=0)
    check("completed (a skip exists)", runner.run(7) == "completed")

    print("== cancel before start ==")
    rec = JobRecord(id=8, selected_albums=["A"], folder_structure=["{album}"])
    store = FakeJobStore(rec, cancel=True)
    dl = FakeDownloader()
    runner, _ = make_runner(store, FakeICloud({"A": [FakePhoto("p", "p.jpg")]}), dl)
    check("status cancelled", runner.run(8) == "cancelled")
    check("nothing downloaded", len(dl.calls) == 0)

    print("== lock held → abort ==")
    rec = JobRecord(id=9, selected_albums=["A"], folder_structure=["{album}"])
    store = FakeJobStore(rec)
    pub = FakePublisher()
    runner = JobRunner(job_store=store, lock=FakeLock(held=True),
                       downloader_factory=lambda: (FakeDownloader(), lambda: None),
                       icloud=FakeICloud({}), publisher=pub, sleep=lambda s: None)
    check("status failed on lock held", runner.run(9) == "failed")
    check("abort logged", any(e.get("level") == "error" for e in pub.events))

    print("== concurrency (6 assets, 3 workers) ==")
    rec = JobRecord(id=10, selected_albums=["A"], folder_structure=["{album}"])
    photos = [FakePhoto(f"c{i}", f"c{i}.jpg") for i in range(6)]
    store = FakeJobStore(rec)
    dl = FakeDownloader()
    runner, _ = make_runner(store, FakeICloud({"A": photos}), dl, concurrency=3)
    check("all 6 downloaded", runner.run(10) == "completed" and store.counts == (6, 0, 0))
    check("each called once", all(dl.calls[f"c{i}"] == 1 for i in range(6)))

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
