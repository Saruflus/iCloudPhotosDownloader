"""Optional shared-secret gate for the API (defense-in-depth on a LAN).

If `API_SHARED_SECRET` is set, every request to a protected router must send the
matching `X-Sync-Secret` header. If it's unset (default), the check is a no-op.
This is not real auth — the app is LAN-only by design (see Security Notes) — just
a cheap barrier against casual access.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


async def require_secret(x_sync_secret: str | None = Header(default=None)) -> None:
    secret = get_settings().api_shared_secret
    if secret and x_sync_secret != secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Sync-Secret header",
        )
