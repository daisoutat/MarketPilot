"""Async engine + session dependency (shared by API and workers)."""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from .config import get_settings, resolve_url_from_secret_arn
from .models import make_engine, make_sessionmaker

_engine = None
_session_factory: sessionmaker | None = None


def init_db(database_url: str | None = None):
    global _engine, _session_factory
    url = database_url or get_settings().database_url
    if not url and get_settings().db_secret_arn:
        url = resolve_url_from_secret_arn(get_settings().db_secret_arn)
    if not url:
        return None
    _engine = make_engine(url)
    _session_factory = make_sessionmaker(_engine)
    return _engine


def get_engine():
    if _engine is None:
        init_db()
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        init_db()
    if _session_factory is None:
        raise RuntimeError("database not configured (set MP_DATABASE_URL or MP_DB_SECRET_ARN)")
    async with _session_factory() as session:  # type: ignore[union-attr]
        yield session


async def ping() -> bool:
    """Return True when the database answers `SELECT 1`."""
    if _engine is None:
        init_db()
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False