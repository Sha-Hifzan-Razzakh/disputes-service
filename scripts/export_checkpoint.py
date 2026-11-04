"""Export the latest checkpoint of an assist thread as a JSON fixture.

    uv run python scripts/export_checkpoint.py assist:<dispute_id> tests/fixtures/checkpoints/v1_assist.json

The fixture records the state values and checkpoint metadata so later releases can test that
threads created by this version still resume.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app import __version__
from app.bootstrap.settings import get_settings


async def export(thread_id: str, out: str) -> None:
    dsn = get_settings().checkpoint_dsn
    async with AsyncConnectionPool(dsn, kwargs={"autocommit": True, "row_factory": dict_row}) as pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        tup = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
        if tup is None:
            raise SystemExit(f"no checkpoint for {thread_id}")
        doc = {
            "app_version": __version__,
            "graph": "assist",
            "state_schema": "assist-v1",
            "thread_id": thread_id,
            "checkpoint_id": tup.checkpoint["id"],
            "values": tup.checkpoint["channel_values"],
            "metadata": tup.metadata,
        }
    Path(out).write_text(json.dumps(doc, indent=2, default=str, sort_keys=True))  # noqa: ASYNC240
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(export(sys.argv[1], sys.argv[2]))
