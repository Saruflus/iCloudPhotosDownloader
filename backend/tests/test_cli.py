#!/usr/bin/env python3
"""CLI tests — typer CliRunner with monkeypatched service/helpers.

Run:  cd backend && PYTHONPATH=. python tests/test_cli.py
"""
from __future__ import annotations

import sys

from typer.testing import CliRunner

from app import cli

PASSED = 0
runner = CliRunner()


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeSvc:
    def __init__(self, requires_2fa=False, twofa_ok=True):
        self._requires = requires_2fa
        self._twofa_ok = twofa_ok
        self.authed = False

    def authenticate(self, apple_id, password):
        self.authed = not self._requires
        return self._requires

    def submit_2fa(self, code):
        self.authed = self._twofa_ok
        return self._twofa_ok

    def get_status(self):
        return {"authenticated": self.authed, "needs_2fa": not self.authed}


def run() -> None:
    print("== auth (no 2FA) ==")
    svc = FakeSvc(requires_2fa=False)
    cli.get_icloud_service = lambda: svc
    res = runner.invoke(cli.app, ["auth"], input="me@example.com\nhunter2\n")
    check("exit 0", res.exit_code == 0)
    check("authenticated reported", "Authenticated: True" in res.stdout)

    print("== auth (2FA success) ==")
    svc = FakeSvc(requires_2fa=True, twofa_ok=True)
    cli.get_icloud_service = lambda: svc
    res = runner.invoke(cli.app, ["auth"], input="me@example.com\nhunter2\n123456\n")
    check("2FA exit 0", res.exit_code == 0)
    check("authenticated after 2FA", "Authenticated: True" in res.stdout)

    print("== auth (2FA fail) ==")
    svc = FakeSvc(requires_2fa=True, twofa_ok=False)
    cli.get_icloud_service = lambda: svc
    res = runner.invoke(cli.app, ["auth"], input="me@example.com\nhunter2\n000000\n")
    check("2FA fail exit 1", res.exit_code == 1)
    check("failure message", "2FA validation failed" in res.stdout)

    print("== sync ==")
    captured = {}

    def fake_sync(albums):
        captured["albums"] = albums
        return 42

    cli.create_sync_job = fake_sync
    res = runner.invoke(cli.app, ["sync", "--album", "Fuji", "-a", "Holidays"])
    check("sync exit 0", res.exit_code == 0)
    check("albums passed", captured["albums"] == ["Fuji", "Holidays"])
    check("job id echoed", "Enqueued download job 42" in res.stdout)

    print("== sync error (no config) ==")
    def _raise(albums):
        raise RuntimeError("No --album given and no saved schedule config to sync.")
    cli.create_sync_job = _raise
    res = runner.invoke(cli.app, ["sync"])
    check("sync error exit 1", res.exit_code == 1)
    check("error shown", "no saved schedule config" in res.stdout)

    print("== status ==")
    cli.latest_status = lambda: {
        "last_job": {"id": 7, "status": "completed", "downloaded": 10,
                     "skipped": 2, "failed": 0, "total": 12},
        "schedule": {"enabled": True, "cron": "0 2 * * *",
                     "last_run": "2026-06-07", "next_run": "2026-06-08"},
    }
    res = runner.invoke(cli.app, ["status"])
    check("status exit 0", res.exit_code == 0)
    check("job line", "Last job #7: completed" in res.stdout)
    check("schedule line", "Schedule (enabled): 0 2 * * *" in res.stdout)

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
