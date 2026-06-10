"""Shared FastAPI dependencies (so a single override in tests covers all routers)."""
from __future__ import annotations

from app.services.icloud import ICloudService, get_icloud_service


def get_service() -> ICloudService:
    """Injectable ICloudService accessor (overridden in tests)."""
    return get_icloud_service()
