"""Builds and holds process-wide dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.bootstrap.settings import Settings
from app.infrastructure.db.session import make_engine, make_sessionmaker
from app.infrastructure.drafting import AnthropicDrafter, RuleBasedDrafter
from app.infrastructure.retrieval import HashingEmbedder, SqlPolicyStore


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    checkpoint_pool: AsyncConnectionPool
    policy_store: SqlPolicyStore
    drafter: Any
    assist_graph: Any = None


_container: Container | None = None


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("container not initialised; is the app lifespan running?")
    return _container


def set_container(c: Container | None) -> None:
    global _container
    _container = c


async def build_container(s: Settings) -> Container:
    engine = make_engine(s.database_url.get_secret_value(), pool_size=s.db_pool_size)
    sessions = make_sessionmaker(engine)
    pool = AsyncConnectionPool(
        conninfo=s.checkpoint_dsn,
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    await pool.open()
    drafter: Any
    if s.model_provider == "anthropic" and s.anthropic_api_key:
        drafter = AnthropicDrafter(s.model_name, s.anthropic_api_key.get_secret_value(), s.model_timeout_s)
    else:
        drafter = RuleBasedDrafter()
    store = SqlPolicyStore(sessions, HashingEmbedder(s.embedding_dim))
    return Container(
        settings=s, engine=engine, sessions=sessions, checkpoint_pool=pool, policy_store=store, drafter=drafter
    )


async def close_container(c: Container) -> None:
    await c.checkpoint_pool.close()
    await c.engine.dispose()
