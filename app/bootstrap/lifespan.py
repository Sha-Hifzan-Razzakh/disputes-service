from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.bootstrap.container import build_container, close_container, set_container
from app.bootstrap.settings import get_settings
from app.orchestration.assist import build_assist_graph

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    c = await build_container(settings)
    saver = AsyncPostgresSaver(c.checkpoint_pool)  # type: ignore[arg-type]
    await saver.setup()
    c.assist_graph = build_assist_graph().compile(checkpointer=saver)
    set_container(c)
    app.state.container = c
    log.info("startup", env=settings.env, model_provider=settings.model_provider)
    try:
        yield
    finally:
        set_container(None)
        await close_container(c)
        log.info("shutdown")
