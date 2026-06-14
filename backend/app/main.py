"""FastAPI application entrypoint.

Run a SINGLE worker (the iCloud session + scheduler assume one process):
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import albums, auth, jobs, schedule, settings as settings_api, ws
from app.core.config import AVAILABLE_TOKENS
from app.services.icloud import get_icloud_service

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Best-effort passwordless session restore from /config (D2). No-op (and
    # network-free) if no Apple ID was ever remembered.
    try:
        restored = await asyncio.to_thread(get_icloud_service().try_restore)
        LOGGER.info("iCloud session restore on startup: %s", restored)
    except Exception as exc:  # never block startup on iCloud
        LOGGER.warning("iCloud restore skipped: %s", exc)
    yield


app = FastAPI(title="iCloud → NAS Sync", version="0.1.0", lifespan=lifespan)

# LAN-only; CORS is permissive (the optional shared secret is the real gate).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(albums.router)
app.include_router(jobs.router)
app.include_router(schedule.router)
app.include_router(schedule.schedules_router)
app.include_router(settings_api.router)
app.include_router(ws.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict:
    return {"ok": True}


@app.get("/api/tokens", tags=["meta"])
async def tokens() -> list[dict]:
    """Folder-template tokens (the frontend renders these as chips)."""
    return AVAILABLE_TOKENS
