"""Schedule API (/api/schedule) — single saved schedule (D10).

GET current config, PUT to upsert, POST /toggle to enable/disable. Each mutation
publishes a `schedules:reload` signal so the dedicated scheduler process re-reads
(it owns APScheduler; the API never schedules in-process).
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.security import require_secret
from app.services.scheduler import next_run_after, valid_cron

router = APIRouter(prefix="/api/schedule", tags=["schedule"], dependencies=[Depends(require_secret)])


class ScheduleBody(BaseModel):
    cron_expression: str
    job_config: dict = Field(default_factory=dict)
    enabled: bool = True


class ToggleBody(BaseModel):
    enabled: bool


class ScheduleOut(BaseModel):
    id: int
    cron_expression: str
    job_config: dict = Field(default_factory=dict)
    enabled: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class ReloadNotifier(Protocol):
    async def notify(self) -> None: ...


class RedisReloadNotifier:
    def __init__(self, redis) -> None:
        self.r = redis

    async def notify(self) -> None:
        try:
            await self.r.publish("schedules:reload", b"1")
        except Exception:
            pass


def _to_dict(row) -> dict:
    return {
        "id": row.id, "cron_expression": row.cron_expression,
        "job_config": row.job_config or {}, "enabled": row.enabled,
        "last_run_at": row.last_run_at, "next_run_at": row.next_run_at,
    }


class SqlScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self) -> dict | None:
        from app.models.assets import Schedule

        res = await self.s.execute(select(Schedule).order_by(Schedule.id).limit(1))
        row = res.scalars().first()
        return _to_dict(row) if row else None

    async def upsert(self, *, cron, job_config, enabled, next_run) -> dict:
        from app.models.assets import Schedule

        res = await self.s.execute(select(Schedule).order_by(Schedule.id).limit(1))
        row = res.scalars().first()
        if row is None:
            row = Schedule(cron_expression=cron)
            self.s.add(row)
        row.cron_expression = cron
        row.job_config = job_config
        row.enabled = enabled
        row.next_run_at = next_run
        await self.s.commit()
        await self.s.refresh(row)
        return _to_dict(row)

    async def set_enabled(self, enabled, next_run) -> dict | None:
        from app.models.assets import Schedule

        res = await self.s.execute(select(Schedule).order_by(Schedule.id).limit(1))
        row = res.scalars().first()
        if row is None:
            return None
        row.enabled = enabled
        row.next_run_at = next_run
        await self.s.commit()
        await self.s.refresh(row)
        return _to_dict(row)


def get_schedule_repo(session: AsyncSession = Depends(get_async_session)) -> SqlScheduleRepository:
    return SqlScheduleRepository(session)


def get_notifier() -> ReloadNotifier:
    from app.core.redis import get_redis

    return RedisReloadNotifier(get_redis())


@router.get("", response_model=ScheduleOut | None)
async def get_schedule(repo=Depends(get_schedule_repo)):
    return await repo.get()


@router.put("", response_model=ScheduleOut)
async def put_schedule(body: ScheduleBody, repo=Depends(get_schedule_repo),
                       notifier=Depends(get_notifier)) -> dict:
    if not valid_cron(body.cron_expression):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Invalid cron expression: {body.cron_expression!r}")
    nxt = next_run_after(body.cron_expression) if body.enabled else None
    sched = await repo.upsert(cron=body.cron_expression, job_config=body.job_config,
                              enabled=body.enabled, next_run=nxt)
    await notifier.notify()
    return sched


@router.post("/toggle", response_model=ScheduleOut)
async def toggle_schedule(body: ToggleBody, repo=Depends(get_schedule_repo),
                          notifier=Depends(get_notifier)) -> dict:
    current = await repo.get()
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No schedule configured")
    nxt = next_run_after(current["cron_expression"]) if body.enabled else None
    updated = await repo.set_enabled(body.enabled, nxt)
    await notifier.notify()
    return updated
