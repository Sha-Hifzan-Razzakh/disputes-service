"""Retention job (T10.11): delete checkpoints of threads that finished more than APP_CHECKPOINT_RETENTION_DAYS ago.

The business record (dispute timeline, approvals, side_effects, audit_log) is kept; only the execution state goes.
Active threads (running / awaiting_approval / resume_failed) are never touched.

    uv run python scripts/prune_checkpoints.py [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.bootstrap.settings import Settings, get_settings

FINISHED = ("done", "failed", "cancelled")


async def prune(settings: Settings, *, dry_run: bool = False, now: datetime | None = None) -> list[str]:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.checkpoint_retention_days)
    kwargs = {"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0}
    async with AsyncConnectionPool(settings.checkpoint_dsn, kwargs=kwargs, min_size=1, max_size=2) as pool:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT thread_id FROM threads WHERE status = ANY(%s) AND updated_at < %s ORDER BY updated_at",
                    (list(FINISHED), cutoff),
                )
            ).fetchall()
        ids = [r["thread_id"] for r in rows]  # type: ignore[call-overload]
        if not dry_run:
            saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
            for tid in ids:
                await saver.adelete_thread(tid)
    return ids


if __name__ == "__main__":
    pruned = asyncio.run(prune(get_settings(), dry_run="--dry-run" in sys.argv))
    print(f"{'would prune' if '--dry-run' in sys.argv else 'pruned'} {len(pruned)} threads")
