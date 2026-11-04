"""FastAPI lifespan (T07.7): build the container, prepare the checkpointer, compile graphs once, close on exit."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.bootstrap.container import build_container, close_container
from app.bootstrap.settings import get_settings
from app.orchestration.assist import build_assist_graph

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    c = await build_container(settings)
    try:
        saver = AsyncPostgresSaver(c.checkpoint_pool)  # type: ignore[arg-type]
        if settings.checkpoint_auto_setup:
            await saver.setup()  # dev/test only; staging and prod run it as a deploy step
        c.assist_graph = build_assist_graph().compile(checkpointer=saver, name="assist")
        app.state.container = c
        log.info("startup", env=settings.env, model_provider=settings.model_provider)
        yield
    finally:
        await close_container(c)
        log.info("shutdown")
