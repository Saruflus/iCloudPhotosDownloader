"""Settings API (Lot 2) — view effective config, override a whitelisted subset.

GET returns the effective configuration (env + DB overrides merged) plus which
keys are overridden. PUT validates and stores overrides; workers pick them up on
the next job start. Env-only values (paths, connections, secret) are read-only
here by design — they require a container restart anyway.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_async_session
from app.core.overrides import OVERRIDABLE, validate_override
from app.core.security import require_secret

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_secret)])


class SettingsOut(BaseModel):
    # effective values (env merged with overrides)
    download_concurrency: int
    max_retries: int
    local_timezone: str
    thumbnail_cache_ttl: int
    # read-only context
    download_base_path: str
    icloud_config_dir: str
    api_secret_set: bool
    # notification channels configured via env (Lot 4)
    notify_channels: list[str]
    notify_on_success: bool
    notify_on_failure: bool
    # which of the four overridable keys come from the DB
    overridden: list[str]


class SettingsPatch(BaseModel):
    download_concurrency: int | None = None
    max_retries: int | None = None
    local_timezone: str | None = None
    thumbnail_cache_ttl: int | None = None


class SqlSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def all(self) -> dict:
        from app.models.assets import AppSetting

        res = await self.s.execute(select(AppSetting))
        return {row.key: row.value for row in res.scalars().all() if row.key in OVERRIDABLE}

    async def set(self, key: str, value) -> None:
        from app.models.assets import AppSetting

        row = await self.s.get(AppSetting, key)
        if row is None:
            self.s.add(AppSetting(key=key, value=value))
        else:
            row.value = value
        await self.s.commit()

    async def delete(self, key: str) -> None:
        from app.models.assets import AppSetting

        row = await self.s.get(AppSetting, key)
        if row is not None:
            await self.s.delete(row)
            await self.s.commit()


def get_settings_repo(session: AsyncSession = Depends(get_async_session)) -> SqlSettingsRepository:
    return SqlSettingsRepository(session)


def _notify_channels(s) -> list[str]:
    channels = []
    if s.ntfy_url:
        channels.append("ntfy")
    if s.discord_webhook_url:
        channels.append("discord")
    if s.smtp_host and s.smtp_to:
        channels.append("email")
    return channels


def _effective(overrides: dict) -> dict:
    s = get_settings()
    return {
        "download_concurrency": overrides.get("download_concurrency", s.download_concurrency),
        "max_retries": overrides.get("max_retries", s.max_retries),
        "local_timezone": overrides.get("local_timezone", s.local_timezone),
        "thumbnail_cache_ttl": overrides.get("thumbnail_cache_ttl", s.thumbnail_cache_ttl),
        "download_base_path": s.download_base_path,
        "icloud_config_dir": s.icloud_config_dir,
        "api_secret_set": bool(s.api_shared_secret),
        "notify_channels": _notify_channels(s),
        "notify_on_success": s.notify_on_success,
        "notify_on_failure": s.notify_on_failure,
        "overridden": sorted(overrides.keys()),
    }


@router.get("", response_model=SettingsOut)
async def get_app_settings(repo=Depends(get_settings_repo)) -> dict:
    return _effective(await repo.all())


@router.put("", response_model=SettingsOut)
async def put_app_settings(body: SettingsPatch, repo=Depends(get_settings_repo)) -> dict:
    for key, value in body.model_dump(exclude_none=True).items():
        try:
            normalized = validate_override(key, value)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{key}: {exc}") from exc
        await repo.set(key, normalized)
    return _effective(await repo.all())


@router.delete("/{key}", response_model=SettingsOut)
async def reset_app_setting(key: str, repo=Depends(get_settings_repo)) -> dict:
    if key not in OVERRIDABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown setting: {key}")
    await repo.delete(key)
    return _effective(await repo.all())
