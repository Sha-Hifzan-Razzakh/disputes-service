"""Run the approval-expiry and reconciliation sweep once (for an external scheduler / cron job).

uv run python scripts/sweep.py
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from app.bootstrap.lifespan import lifespan
from app.bootstrap.sweeper import Sweeper


async def main() -> None:
    app = FastAPI()
    async with lifespan(app, start_sweeper=False):
        print(await Sweeper(app.state.container).run_once())


if __name__ == "__main__":
    asyncio.run(main())
