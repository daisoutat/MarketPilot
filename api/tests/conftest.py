"""Shared fixtures for MarketPilot tests.

Sets authentication to dev mode and runs the data layer on an in-memory
SQLite store (StaticPool => single shared connection, schema created once).
"""
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_PROJECT_ROOT = _here.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("MP_AUTH_MODE", "dev")
os.environ["MP_DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ.pop("MP_DB_SECRET_ARN", None)

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from common import db as common_db  # noqa: E402
from common.models import Base, Marketplace  # noqa: E402


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_session(session_factory):
    async with session_factory() as session:
        session.add_all(
            [
                Marketplace(code="US", marketplace_id="ATVPDKIKX0DER", currency="USD", iso="en-US"),
                Marketplace(code="CA", marketplace_id="A2VIGQ35RCS4UG", currency="CAD", iso="fr-CA"),
            ]
        )
        await session.commit()
        yield session


@pytest_asyncio.fixture
async def app_client(seeded_session):
    """FastAPI TestClient with the dashboard `get_session` dependency pointed
    at the same in-memory store as `seeded_session`."""
    from fastapi.testclient import TestClient

    from api.app.main import app

    async def override_get_session():
        yield seeded_session

    app.dependency_overrides[common_db.get_session] = override_get_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(common_db.get_session, None)