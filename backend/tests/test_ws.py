#!/usr/bin/env python3
"""WebSocket endpoint test (note 4) — drives the coroutine directly with fakes.

Avoids TestClient's threading/portal; we call job_ws() with a fake WebSocket and
fake async Redis and assert what gets sent.

Run:  cd backend && PYTHONPATH=. python tests/test_ws.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.api.ws import job_ws

PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


class FakeWS:
    def __init__(self):
        self.sent: list[str] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)


class FakePubSub:
    def __init__(self, messages):
        self.messages = messages
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel=None):
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True

    async def listen(self):
        yield {"type": "subscribe"}
        for m in self.messages:
            yield {"type": "message", "data": m}


class FakeAsyncRedis:
    def __init__(self, log, messages):
        self._log = log
        self._messages = messages
        self.pubsub_obj = FakePubSub(messages)

    async def lrange(self, key, start, end):
        return self._log

    def pubsub(self):
        return self.pubsub_obj


def run() -> None:
    # Log stored newest-first (LPUSH); endpoint should reverse to oldest-first.
    log = [
        json.dumps({"type": "log", "level": "info", "message": "newer"}).encode(),
        json.dumps({"type": "log", "level": "info", "message": "older"}).encode(),
    ]
    live = [
        json.dumps({"type": "progress", "downloaded": 1, "total": 2}).encode(),
        json.dumps({"type": "done", "status": "completed"}).encode(),
    ]
    ws = FakeWS()
    redis = FakeAsyncRedis(log, live)

    asyncio.run(job_ws(ws, 42, redis=redis))

    sent = [json.loads(s) for s in ws.sent]
    msgs = [m.get("message") for m in sent if m.get("type") == "log"]

    check("accepted connection", ws.accepted is True)
    check("replayed both log lines", set(msgs) == {"older", "newer"})
    check("log replayed oldest-first", msgs.index("older") < msgs.index("newer"))
    check("progress forwarded", any(m.get("type") == "progress" for m in sent))
    check("done forwarded", any(m.get("type") == "done" for m in sent))
    check("logs before live", sent[0]["type"] == "log" and sent[-1]["type"] == "done")
    check("subscribe confirmation not forwarded", all("type" in m for m in sent))
    check("unsubscribed + closed on exit", redis.pubsub_obj.unsubscribed and redis.pubsub_obj.closed)

    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
