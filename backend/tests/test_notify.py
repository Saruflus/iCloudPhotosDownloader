#!/usr/bin/env python3
"""Notification + scheduler-2FA tests (Lot 4).

Run:  cd backend && PYTHONPATH=. python tests/test_notify.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.notify import Notifier, notify_job_result  # noqa: E402
from app.services.scheduler import SchedulerService  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeHttp:
    def __init__(self, fail: bool = False):
        self.posts: list[dict] = []
        self.fail = fail

    def post(self, url, **kw):
        if self.fail:
            raise RuntimeError("network down")
        self.posts.append({"url": url, **kw})


class FakeSmtp:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSmtp.sent.append(msg)


def run() -> None:
    print("== Notifier channels ==")
    http = FakeHttp()
    n = Notifier(ntfy_url="https://ntfy.sh/t", discord_webhook_url="https://discord/x",
                 smtp={"host": "mail", "port": 587, "from": "a@b", "to": "c@d"},
                 http=http, smtp_factory=FakeSmtp)
    check("configured", n.configured is True)
    n.send("Job #1 failed", "boom", level="error")
    check("ntfy posted", any("ntfy.sh" in p["url"] for p in http.posts))
    check("ntfy priority high on error",
          any(p.get("headers", {}).get("Priority") == "high" for p in http.posts))
    check("discord posted", any("discord" in p["url"] for p in http.posts))
    check("email sent", len(FakeSmtp.sent) == 1)
    check("email subject", "[iCloud Sync] Job #1 failed" in FakeSmtp.sent[0]["Subject"])

    print("== failures never raise ==")
    bad = Notifier(ntfy_url="https://ntfy.sh/t", http=FakeHttp(fail=True))
    bad.send("x", "y")  # would raise without the guard
    check("dead webhook swallowed", True)
    check("unconfigured → not configured", Notifier().configured is False)

    print("== notify_job_result gating ==")
    http = FakeHttp()
    n = Notifier(ntfy_url="https://n/t", http=http)
    notify_job_result(n, 1, "completed", {"downloaded": 5}, on_success=False, on_failure=True)
    check("success silenced by default", len(http.posts) == 0)
    notify_job_result(n, 1, "completed", {"downloaded": 5}, on_success=True, on_failure=True)
    check("success sent when opted in", len(http.posts) == 1)
    notify_job_result(n, 2, "failed", {"failed": 3}, on_success=False, on_failure=True)
    check("failure sent", len(http.posts) == 2)
    notify_job_result(n, 3, "failed", None, on_success=False, on_failure=False)
    check("failure silenced when opted out", len(http.posts) == 2)

    print("== scheduler skips + notifies on expired session (Lot 4) ==")

    class FakeNotifier:
        def __init__(self):
            self.sent = []

        def send(self, title, message, level="info"):
            self.sent.append((title, level))

    class BoomSession:
        def __call__(self):
            raise AssertionError("must not touch the DB when auth is down")

    fn = FakeNotifier()
    svc = SchedulerService(scheduler=None, session_factory=BoomSession(),
                           enqueue=lambda jid: "t", auth_ok=lambda: False, notifier=fn)
    svc._fire(1)
    check("fire skipped without auth", len(fn.sent) == 1)
    check("needs-2fa alert is an error", fn.sent[0][1] == "error")

    print("== scheduler reload keeps foreign jobs (heartbeat) ==")

    class FakeJob:
        def __init__(self, id):
            self.id = id

    class FakeScheduler:
        def __init__(self):
            self.jobs = [FakeJob("schedule-1"), FakeJob("heartbeat")]
            self.removed = []

        def get_jobs(self):
            return list(self.jobs)

        def remove_job(self, jid):
            self.removed.append(jid)
            self.jobs = [j for j in self.jobs if j.id != jid]

        def add_job(self, *a, **kw):
            pass

    class EmptySession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, model):
            class Q:
                @staticmethod
                def filter_by(**kw):
                    return Q

                @staticmethod
                def all():
                    return []
            return Q

    fs = FakeScheduler()
    svc2 = SchedulerService(scheduler=fs, session_factory=lambda: EmptySession(),
                            enqueue=lambda jid: "t")
    svc2.reload()
    check("schedule job removed", "schedule-1" in fs.removed)
    check("heartbeat preserved", "heartbeat" not in fs.removed)

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
