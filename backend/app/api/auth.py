"""Auth API (/api/auth) — wraps ICloudService behind FastAPI.

pyicloud is blocking, so every call is offloaded with `asyncio.to_thread` (D1).
The password is used only to build the session and is never stored (D2 / Security).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_service
from app.core.security import require_secret
from app.services.icloud import ICloudError, ICloudService

router = APIRouter(prefix="/api/auth", tags=["auth"], dependencies=[Depends(require_secret)])


class LoginBody(BaseModel):
    apple_id: str
    password: str


class TwoFABody(BaseModel):
    code: str


class StatusResponse(BaseModel):
    authenticated: bool
    needs_2fa: bool


class LoginResponse(BaseModel):
    requires_2fa: bool


@router.get("/status", response_model=StatusResponse)
async def get_status(service: ICloudService = Depends(get_service)) -> dict:
    return await asyncio.to_thread(service.get_status)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginBody, service: ICloudService = Depends(get_service)
) -> dict:
    try:
        requires_2fa = await asyncio.to_thread(
            service.authenticate, body.apple_id, body.password
        )
    except Exception as exc:  # pyicloud raises various errors on bad creds
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"iCloud login failed: {exc}",
        ) from exc
    return {"requires_2fa": requires_2fa}


@router.post("/2fa")
async def submit_2fa(
    body: TwoFABody, service: ICloudService = Depends(get_service)
) -> dict:
    try:
        ok = await asyncio.to_thread(service.submit_2fa, body.code)
    except ICloudError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid 2FA code"
        )
    return {"success": True}


@router.post("/logout")
async def logout(service: ICloudService = Depends(get_service)) -> dict:
    await asyncio.to_thread(service.logout)
    return {"success": True}
