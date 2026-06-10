"""WebSocket endpoint — live job progress (note 4).

Bridges Celery → browser via Redis: on connect we replay the stored log
(`icloud:job:{id}:log`, newest-first via LPUSH so we reverse it), then subscribe
to `icloud:job:{id}:progress` and forward each message. Replaying first means a
refresh or late-join doesn't lose history.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core.redis import get_redis

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["ws"])


def _text(raw) -> str:
    return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)


@router.websocket("/ws/jobs/{job_id}")
async def job_ws(websocket: WebSocket, job_id: int, redis=Depends(get_redis)) -> None:
    await websocket.accept()
    log_key = f"icloud:job:{job_id}:log"
    channel = f"icloud:job:{job_id}:progress"

    # 1. Replay stored log (oldest-first).
    try:
        entries = await redis.lrange(log_key, 0, -1)
        for raw in reversed(entries):
            await websocket.send_text(_text(raw))
    except Exception as exc:
        LOGGER.debug("log replay failed for job %s: %s", job_id, exc)

    # 2. Stream live progress.
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                await websocket.send_text(_text(message["data"]))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # client gone / redis hiccup
        LOGGER.debug("ws stream ended for job %s: %s", job_id, exc)
    finally:
        try:
            await pubsub.unsubscribe(channel)
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close:
                await close()
        except Exception:
            pass
