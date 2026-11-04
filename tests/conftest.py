"""Test fixtures: a fresh migrated + seeded Postgres database per test session.

Uses APP_TEST_DATABASE_URL (default: local Postgres on localhost:5432).
"""

from __future__ import annotations

import os

# Checkpoints must deserialize without importing arbitrary classes (ADR-0002); read at import time by langgraph.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
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
    os.environ["APP_SWEEPER_ENABLED"] = "false"  # tests drive the sweeper explicitly
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
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.main import create_app

    application = create_app()
    application.state.span_exporter = InMemorySpanExporter()  # picked up by setup_telemetry in the lifespan
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


@pytest.fixture
async def mcp_tools(app: object) -> AsyncIterator[dict[str, object]]:
    """Real McpToolGateway over in-memory MCP servers (fresh ledgers per test), swapped into the graph services."""
    import dataclasses

    from mcp.client.client import Client

    from app.infrastructure.tools.gateway import McpToolGateway, TokenBucket
    from app.infrastructure.tools.mcp_client import McpConnection
    from mcp_servers import network_gateway, payments

    ledger = payments.PaymentsLedger.from_seeds()
    store = network_gateway.NetworkGatewayStore()
    servers = {"payments": payments.create_server(ledger), "network-gateway": network_gateway.create_server(store)}
    conns = {name: McpConnection(name, lambda s=srv: Client(s), 4.0) for name, srv in servers.items()}
    for conn in conns.values():
        await conn.start()
    c = app.state.container  # type: ignore[attr-defined]
    gateway = McpToolGateway(
        connections=conns,
        approvals=c.graph_services.approvals,
        sessions=c.sessions,
        limiter=TokenBucket(rate=100, burst=100),
        max_attempts=3,
        backoff_s=0.01,
    )
    original = c.graph_services
    c.graph_services = dataclasses.replace(original, tools=gateway)
    try:
        yield {"gateway": gateway, "ledger": ledger, "network": store, "connections": conns}
    finally:
        c.graph_services = original
        for conn in conns.values():
            await conn.close()
