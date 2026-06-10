"""Database engines and sessions (D1).

Two separate worlds share one declarative ``Base``:

* FastAPI uses the **async** engine (asyncpg) via the ``get_async_session``
  dependency.
* Celery tasks and the scheduler service use the **sync** engine (psycopg) via
  the ``sync_session`` context manager.

Engines are created lazily so importing this module never requires a live DB
(needed for Alembic offline mode and for unit tests).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all models."""


@lru_cache
def async_engine():
    return create_async_engine(get_settings().async_database_url, pool_pre_ping=True)


@lru_cache
def sync_engine():
    return create_engine(get_settings().sync_database_url, pool_pre_ping=True)


@lru_cache
def _async_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        async_engine(), expire_on_commit=False, class_=AsyncSession
    )


@lru_cache
def _sync_session_factory() -> sessionmaker[Session]:
    return sessionmaker(sync_engine(), expire_on_commit=False, class_=Session)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async session (D1)."""
    async with _async_session_factory()() as session:
        yield session


@contextmanager
def sync_session() -> Iterator[Session]:
    """Context manager for Celery tasks / scheduler service (D1).

    Commits on clean exit, rolls back on exception, always closes.
    """
    session = _sync_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
