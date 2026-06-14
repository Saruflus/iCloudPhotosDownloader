#!/usr/bin/env python3
"""ThrottleGate + DiskThumbCache tests (Lot 3).

Run:  cd backend && PYTHONPATH=. python tests/test_throttle.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.icloud import ThrottleGate, _is_throttle_error  # noqa: E402
from app.services.thumbs import DiskThumbCache  # noqa: E402

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def run() -> None:
    print("== ThrottleGate ==")
    clock = FakeClock()
    gate = ThrottleGate(base=30, cap=120, sleep=clock.sleep, now=clock.now)

    gate.wait()
    check("no cooldown → wait is a no-op", clock.slept == [])

    check("first trip → base delay", gate.trip() == 30)
    gate.wait()
    check("wait slept through the cooldown", sum(clock.slept) >= 30)

    check("second trip doubles", gate.trip() == 60)
    check("third trip caps", gate.trip() == 120)
    check("fourth stays capped", gate.trip() == 120)

    gate.clear()
    clock.slept.clear()
    gate.wait()
    check("clear resets the gate", clock.slept == [])
    check("after clear, trip restarts at base", gate.trip() == 30)

    print("== _is_throttle_error ==")

    class RespErr(Exception):
        def __init__(self, status):
            class R:  # noqa: N801
                status_code = status
            self.response = R()

    class CodeErr(Exception):
        def __init__(self, code):
            self.code = code

    check("requests 429 → throttle", _is_throttle_error(RespErr(429)) is True)
    check("requests 503 → throttle", _is_throttle_error(RespErr(503)) is True)
    check("requests 500 → not", _is_throttle_error(RespErr(500)) is False)
    check("pyicloud code 429 → throttle", _is_throttle_error(CodeErr(429)) is True)
    check("plain error → not", _is_throttle_error(RuntimeError("x")) is False)
    check("non-numeric code → not", _is_throttle_error(CodeErr("boom")) is False)

    print("== DiskThumbCache ==")
    with tempfile.TemporaryDirectory() as tmp:
        cache = DiskThumbCache(Path(tmp) / "thumbs")
        check("miss → None", cache.get("A1") is None)
        cache.put("A1", b"jpegbytes")
        check("hit after put", cache.get("A1") == b"jpegbytes")
        cache.put("A1", b"newer")
        check("overwrite", cache.get("A1") == b"newer")
        check("ids are hashed (no traversal)", cache.get("../../etc/passwd") is None)
        cache.put("../../etc/passwd", b"x")
        inside = list((Path(tmp) / "thumbs").rglob("*.jpg"))
        check("hostile id stays inside the root", len(inside) == 2)
        check("no .part leftovers", not list((Path(tmp) / "thumbs").rglob("*.part")))

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
