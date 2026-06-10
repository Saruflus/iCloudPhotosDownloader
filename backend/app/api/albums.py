"""Albums API — browse albums, list assets, serve (cached) thumbnails.

Thumbnail strategy (D13): bytes are cached in Redis (`thumb:{id}`, TTL
`THUMBNAIL_CACHE_TTL`). On a miss we look the asset up in ICloudService's
in-process cache (populated when its album was listed) and download the `thumb`
rendition. If the asset was never listed in this process, return 404 — the grid
will have listed it before requesting its thumbnail.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.api.deps import get_service
from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import require_secret
from app.services.icloud import ICloudError, ICloudService

router = APIRouter(prefix="/api", tags=["albums"], dependencies=[Depends(require_secret)])


class AlbumOut(BaseModel):
    name: str
    asset_count: int | None = None


class AssetOut(BaseModel):
    asset_id: str
    filename: str
    media_type: str | None
    file_size: int | None
    created_at: datetime | None
    is_live_photo: bool
    has_edited_version: bool
    thumbnail_url: str


@router.get("/albums", response_model=list[AlbumOut])
async def list_albums(service: ICloudService = Depends(get_service)) -> list:
    return await asyncio.to_thread(service.get_albums)


@router.get("/albums/{name}/assets", response_model=list[AssetOut])
async def album_assets(
    name: str,
    offset: int = 0,
    limit: int = 50,
    service: ICloudService = Depends(get_service),
) -> list:
    offset = max(0, offset)
    limit = max(1, min(limit, 200))
    try:
        assets = await asyncio.to_thread(service.get_assets, name, offset, limit)
    except ICloudError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        AssetOut(
            asset_id=a.asset_id,
            filename=a.filename,
            media_type=a.media_type,
            file_size=a.file_size,
            created_at=a.created_at,
            is_live_photo=a.is_live_photo,
            has_edited_version=a.has_edited_version,
            thumbnail_url=f"/api/assets/{a.asset_id}/thumbnail",
        )
        for a in assets
    ]


@router.get("/assets/{asset_id}/thumbnail")
async def asset_thumbnail(
    asset_id: str,
    service: ICloudService = Depends(get_service),
    redis=Depends(get_redis),
) -> Response:
    key = f"thumb:{asset_id}"
    try:
        cached = await redis.get(key)
    except Exception:  # Redis down → just fetch live
        cached = None
    if cached:
        return Response(content=cached, media_type="image/jpeg")

    data = await asyncio.to_thread(service.thumbnail_for, asset_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not loaded; browse its album first",
        )
    try:
        await redis.set(key, data, ex=get_settings().thumbnail_cache_ttl)
    except Exception:
        pass
    return Response(content=data, media_type="image/jpeg")
