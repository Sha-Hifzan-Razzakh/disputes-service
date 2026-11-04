"""Test fixtures: a fresh migrated + seeded Postgres database per test session.

Uses APP_TEST_DATABASE_URL (default: local Postgres on localhost:5432).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable

import httpx
import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager

BASE_DSN = os.environ.get("APP_TEST_DSN", "postgresql://postgres@localhost:5432")
TEST_DB = "disputes_test"
SECRET = "test-secret-at-least-thirty-two-bytes-long"


@pytest.fixture(scope="session")
def database_url() -> str:
    with psycopg.connect(f"{BASE_DSN}/postgres", autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    url = f"{BASE_DSN.replace('postgresql://', 'postgresql+psycopg://')}/{TEST_DB}"
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    os.environ["APP_DATABASE_URL"] = url
    os.environ["APP_JWT_SECRET"] = SECRET
    os.environ["APP_MODEL_PROVIDER"] = "rules"
    from app.bootstrap.settings import get_settings

    get_settings.cache_clear()
    return url


@pytest.fixture(scope="session")
async def seeded(database_url: str) -> dict[str, list[str]]:
    from app.infrastructure.db.session import make_engine, make_sessionmaker
    from seeds.load import load

    engine = make_engine(database_url)
    try:
        return await load(make_sessionmaker(engine))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def app(seeded: dict[str, list[str]]) -> AsyncIterator[object]:
    from app.main import create_app

    application = create_app()
    async with LifespanManager(application):
        yield application


@pytest.fixture(scope="session")
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        yield c


@pytest.fixture(scope="session")
def auth() -> Callable[..., dict[str, str]]:
    def make(tenant: str = "northwind", role: str = "analyst", user: str = "ana") -> dict[str, str]:
        token = jwt.encode({"sub": user, "tenant": tenant, "role": role}, SECRET, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    return make
