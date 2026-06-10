#!/usr/bin/env python3
"""Lease lock tests (D4) — fake sync Redis. Run: PYTHONPATH=. python tests/test_locks.py"""
from __future__ import annotations

import sys

from app.core.locks import LeaseLock, LockHeld

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v.encode() if isinstance(v, str) else v
        return True

    def get(self, k):
        return self.store.get(k)

    def expire(self, k, ex):
        return 1 if k in self.store else 0

    def delete(self, k):
        self.store.pop(k, None)


def run() -> None:
    lock = LeaseLock(FakeRedis(), ttl=60, heartbeat=999)

    print("== acquire / contention ==")
    check("first acquire", lock.acquire("o1") is True)
    check("second acquire blocked", lock.acquire("o2") is False)
    check("owner is o1", lock.current_owner() == "o1")

    print("== renew / release ==")
    check("owner renews", lock.renew("o1") is True)
    check("non-owner cannot renew", lock.renew("o2") is False)
    lock.release("o2")
    check("non-owner release is no-op", lock.current_owner() == "o1")
    lock.release("o1")
    check("owner release frees", lock.current_owner() is None)
    check("re-acquire after release", lock.acquire("o2") is True)

    print("== hold() context manager ==")
    lock2 = LeaseLock(FakeRedis(), ttl=60, heartbeat=999)
    with lock2.hold("h1") as owner:
        check("held during context", lock2.current_owner() == "h1" and owner == "h1")
    check("released after context", lock2.current_owner() is None)

    print("== hold() raises when held ==")
    lock3 = LeaseLock(FakeRedis(), ttl=60, heartbeat=999)
    lock3.acquire("squatter")
    try:
        with lock3.hold("me"):
            check("should not enter", False)
    except LockHeld as e:
        check("LockHeld raised with owner", e.owner == "squatter")

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
