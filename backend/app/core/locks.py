"""Redis lease lock with heartbeat (D4).

A single job may run at a time. Acquire with a short TTL, renew via a background
heartbeat while running, release on exit. If the worker dies the lock self-expires
within `ttl` seconds instead of blocking indefinitely. Uses the sync Redis client.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager

LOGGER = logging.getLogger(__name__)
LOCK_KEY = "icloud:sync_lock"


class LockHeld(RuntimeError):
    def __init__(self, owner: str | None) -> None:
        super().__init__(f"sync lock already held by {owner!r}")
        self.owner = owner


class LeaseLock:
    def __init__(self, redis, key: str = LOCK_KEY, ttl: int = 60, heartbeat: int = 20) -> None:
        self.r = redis
        self.key = key
        self.ttl = ttl
        self.heartbeat = heartbeat

    @staticmethod
    def _decode(raw) -> str | None:
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    def acquire(self, owner: str) -> bool:
        return bool(self.r.set(self.key, owner, nx=True, ex=self.ttl))

    def current_owner(self) -> str | None:
        return self._decode(self.r.get(self.key))

    def renew(self, owner: str) -> bool:
        # Small GET→EXPIRE race is acceptable for a single-job lock.
        if self.current_owner() == owner:
            self.r.expire(self.key, self.ttl)
            return True
        return False

    def release(self, owner: str) -> None:
        if self.current_owner() == owner:
            self.r.delete(self.key)

    @contextmanager
    def hold(self, owner: str | None = None):
        """Acquire (or raise LockHeld), heartbeat in the background, release on exit."""
        owner = owner or f"owner-{uuid.uuid4().hex[:12]}"
        if not self.acquire(owner):
            raise LockHeld(self.current_owner())

        stop = threading.Event()

        def _beat() -> None:
            while not stop.wait(self.heartbeat):
                try:
                    self.renew(owner)
                except Exception as exc:  # never let the heartbeat crash the job
                    LOGGER.warning("lease renew failed: %s", exc)

        thread = threading.Thread(target=_beat, name="lease-heartbeat", daemon=True)
        thread.start()
        try:
            yield owner
        finally:
            stop.set()
            thread.join(timeout=2.0)
            self.release(owner)
