"""Create or migrate the LangGraph checkpointer tables. Run once per deploy, before the new app version starts,
with a role that has DDL rights; the app itself runs with APP_CHECKPOINT_AUTO_SETUP=false outside dev/test.

    uv run python scripts/checkpoint_setup.py            # uses APP_DATABASE_URL
"""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.bootstrap.settings import get_settings


async def main() -> None:
    dsn = get_settings().checkpoint_dsn
    kwargs = {"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0}
    async with AsyncConnectionPool(dsn, kwargs=kwargs, min_size=1, max_size=1) as pool:
        await AsyncPostgresSaver(pool).setup()  # type: ignore[arg-type]
        async with pool.connection() as conn:
            row = await (await conn.execute("SELECT max(v) AS v FROM checkpoint_migrations")).fetchone()
    print(f"checkpointer schema at migration {row['v'] if row else '?'}")  # type: ignore[call-overload]


if __name__ == "__main__":
    asyncio.run(main())
