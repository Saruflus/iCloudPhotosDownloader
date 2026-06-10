"""Application configuration (D1, D2, D3, D5, D13).

All settings come from environment variables / the ``.env`` file via
pydantic-settings. The single ``DATABASE_URL`` is normalized into both an async
(asyncpg) and a sync (psycopg) DSN so FastAPI and Celery can each use the right
driver (D1).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _with_driver(url: str, driver: str) -> str:
    """Force a specific SQLAlchemy driver on a postgres URL.

    Accepts plain ``postgresql://`` / ``postgres://`` or an existing
    ``postgresql+<driver>://`` form and rewrites it to ``postgresql+<driver>://``.
    Non-postgres URLs are returned unchanged.
    """
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    base = scheme.split("+", 1)[0]
    if base in ("postgres", "postgresql"):
        return f"postgresql+{driver}://{rest}"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Connections (required)
    database_url: str
    redis_url: str

    # Filesystem
    download_base_path: str = "/downloads"
    icloud_config_dir: str = "/config"  # icloudpd/pyicloud_ipd cookie dir (D2)

    # Behaviour
    download_concurrency: int = 4  # D3
    max_retries: int = 3  # D13
    local_timezone: str = "UTC"  # date-token timezone (D5)
    thumbnail_cache_ttl: int = 604800  # 7 days

    # Optional defense-in-depth on a LAN
    api_shared_secret: str | None = None

    @property
    def async_database_url(self) -> str:
        return _with_driver(self.database_url, "asyncpg")

    @property
    def sync_database_url(self) -> str:
        return _with_driver(self.database_url, "psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# Folder-structure tokens exposed via GET /api/tokens
AVAILABLE_TOKENS = [
    {"id": "year", "label": "Year", "example": "2024"},
    {"id": "month", "label": "Month", "example": "06"},
    {"id": "day", "label": "Day", "example": "15"},
    {"id": "album", "label": "Album", "example": "Holidays"},
    {"id": "mediatype", "label": "Media Type", "example": "RAW"},
    {"id": "person", "label": "Person", "example": "Alice"},
    {"id": "make", "label": "Camera Make", "example": "Apple"},
    {"id": "model", "label": "Camera Model", "example": "iPhone 15 Pro"},
    {"id": "filename", "label": "Filename", "example": "IMG_0001"},
]
