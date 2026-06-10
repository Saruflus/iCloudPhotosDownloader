"""Redis clients (D2) — pub/sub, locks, thumbnail cache. Raw bytes (no decode).

FastAPI uses the async client; the Celery worker/scheduler use the sync client.
"""
from __future__ import annotations

from functools import lru_cache

import redis
import redis.asyncio as aioredis

from app.core.config import get_settings


@lru_cache
def get_redis() -> "aioredis.Redis":
    """Process-wide async Redis client (FastAPI). Usable as a FastAPI dependency."""
    return aioredis.from_url(get_settings().redis_url, decode_responses=False)


@lru_cache
def get_sync_redis() -> "redis.Redis":
    """Process-wide sync Redis client (Celery worker / scheduler, D1)."""
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=False)
