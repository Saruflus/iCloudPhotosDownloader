"""Albums API — browse albums, list assets, serve (cached) thumbnails.

Thumbnail strategy (D13 + Lot 3): lookup order is Redis (`thumb:{id}`, TTL
`THUMBNAIL_CACHE_TTL`) → disk cache on /config (survives restarts and the Redis
TTL) → live fetch (in-process PhotoAsset cache, falling back to a direct
CloudKit lookup by id — so a restart no longer 404s). Writes go to both caches.
Listing a page also prefetches the next page's thumbnails in the background.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import get_service
from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import require_secret
from app.services.icloud import ICloudError, ICloudService
from app.services.thumbs import DiskThumbCache

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["albums"], dependencies=[Depends(require_secret)])

PREFETCH_CONCURRENCY = 4
_PREFETCH_INFLIGHT: set[str] = set()


class AlbumOut(BaseModel):
    name: str
    asset_count: int | None = None
    shared: bool = False


class AssetOut(BaseModel):
    asset_id: str
    filename: str
    media_type: str | None
    media_category: str | None
    file_size: int | None
    created_at: datetime | None
    is_live_photo: bool
    has_edited_version: bool
    has_raw_version: bool
    thumbnail_url: str


def get_thumb_cache() -> DiskThumbCache:
    return DiskThumbCache(Path(get_settings().icloud_config_dir) / "thumbs")


def get_prefetch_scheduler():
    """Schedules the next-page warm-up; overridden in tests to run inline."""

    def schedule(coro) -> None:
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no loop (sync test context) — drop silently
            coro.close()

    return schedule


async def _store_thumb(redis, cache: DiskThumbCache, asset_id: str, data: bytes) -> None:
    try:
        await redis.set(f"thumb:{asset_id}", data, ex=get_settings().thumbnail_cache_ttl)
    except Exception:
        pass
    await asyncio.to_thread(cache.put, asset_id, data)


async def prefetch_page(service: ICloudService, redis, cache: DiskThumbCache,
                        name: str, offset: int, limit: int) -> None:
    """Warm the NEXT page's thumbnails (Lot 3) so paging feels instant.
    Best-effort: every failure is swallowed; an in-flight set prevents storms."""
    key = f"{name}:{offset}"
    if key in _PREFETCH_INFLIGHT:
        return
    _PREFETCH_INFLIGHT.add(key)
    try:
        assets = await asyncio.to_thread(service.get_assets, name, offset, limit)
        sem = asyncio.Semaphore(PREFETCH_CONCURRENCY)

        async def warm(asset_id: str) -> None:
            async with sem:
                try:
                    if await redis.get(f"thumb:{asset_id}"):
                        return
                except Exception:
                    pass
                if await asyncio.to_thread(cache.get, asset_id):
                    return
                data = await asyncio.to_thread(service.thumbnail_for, asset_id)
                if data:
                    await _store_thumb(redis, cache, asset_id, data)

        await asyncio.gather(*(warm(a.asset_id) for a in assets))
    except Exception as exc:
        LOGGER.debug("Prefetch %s failed: %s", key, exc)
    finally:
        _PREFETCH_INFLIGHT.discard(key)


@router.get("/albums", response_model=list[AlbumOut])
async def list_albums(
    with_counts: bool = False, service: ICloudService = Depends(get_service)
) -> list:
    """Album list. Counts default to lazy (None) — each count is a separate
    iCloud query; the UI fills them in via /albums/{name}/count."""
    return await asyncio.to_thread(service.get_albums, with_counts)


@router.get("/albums/{name}/count")
async def album_count(name: str, service: ICloudService = Depends(get_service)) -> dict:
    try:
        count = await asyncio.to_thread(service.get_album_count, name)
    except ICloudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"name": name, "asset_count": count}


@router.get("/albums/{name}/assets", response_model=list[AssetOut])
async def album_assets(
    name: str,
    offset: int = 0,
    limit: int = 50,
    prefetch: bool = True,
    service: ICloudService = Depends(get_service),
    redis=Depends(get_redis),
    cache: DiskThumbCache = Depends(get_thumb_cache),
    schedule=Depends(get_prefetch_scheduler),
) -> list:
    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    try:
        assets = await asyncio.to_thread(service.get_assets, name, offset, limit)
    except ICloudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if prefetch and len(assets) == limit:  # a full page → there may be a next one
        schedule(prefetch_page(service, redis, cache, name, offset + limit, limit))
    return [
        AssetOut(
            asset_id=a.asset_id,
            filename=a.filename,
            media_type=a.media_type,
            media_category=a.media_category,
            file_size=a.file_size,
            created_at=a.created_at,
            is_live_photo=a.is_live_photo,
            has_edited_version=a.has_edited_version,
            has_raw_version=a.has_raw_version,
            thumbnail_url=f"/api/assets/{a.asset_id}/thumbnail",
        )
        for a in assets
    ]


@router.get("/assets/{asset_id}/thumbnail")
async def asset_thumbnail(
    asset_id: str,
    service: ICloudService = Depends(get_service),
    redis=Depends(get_redis),
    cache: DiskThumbCache = Depends(get_thumb_cache),
) -> Response:
    # A thumbnail never changes for a given asset id → let the browser cache it
    # hard; re-display is instant with zero requests.
    cache_headers = {
        "Cache-Control": f"private, max-age={get_settings().thumbnail_cache_ttl}, immutable"
    }
    key = f"thumb:{asset_id}"
    try:
        cached = await redis.get(key)
    except Exception:  # Redis down → fall through to the other layers
        cached = None
    if cached:
        return Response(content=cached, media_type="image/jpeg", headers=cache_headers)

    disk = await asyncio.to_thread(cache.get, asset_id)
    if disk:
        try:  # re-warm Redis so the next hit is fast
            await redis.set(key, disk, ex=get_settings().thumbnail_cache_ttl)
        except Exception:
            pass
        return Response(content=disk, media_type="image/jpeg", headers=cache_headers)

    data = await asyncio.to_thread(service.thumbnail_for, asset_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found in iCloud (or session expired)",
        )
    await _store_thumb(redis, cache, asset_id, data)
    return Response(content=data, media_type="image/jpeg", headers=cache_headers)
