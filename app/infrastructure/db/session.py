from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from opentelemetry import trace
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.application.ports import TransientError

Sessions = async_sessionmaker[AsyncSession]
_tracer = trace.get_tracer("disputes.db")


def make_engine(url: str, pool_size: int = 5, max_overflow: int = 5) -> AsyncEngine:
    return create_async_engine(url, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> Sessions:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(sessions: Sessions) -> AsyncIterator[AsyncSession]:
    """One short transaction. Connection-level failures become TransientError so graph RetryPolicies can retry them
    without importing a driver (import contract: orchestration never imports sqlalchemy)."""
    try:
        with _tracer.start_as_current_span("db.transaction"):
            async with sessions.begin() as s:
                yield s
    except OperationalError as e:
        raise TransientError(str(e.orig or e)) from e
    except DBAPIError as e:
        if e.connection_invalidated:
            raise TransientError(str(e.orig or e)) from e
        raise
