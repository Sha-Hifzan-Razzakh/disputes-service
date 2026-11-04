"""FastAPI lifespan (T07.7): telemetry, container, checkpointer, graphs compiled once, tools, sweeper; closes all."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.bootstrap.container import build_container, close_container, connect_tools
from app.bootstrap.observability import instrument_db, setup_telemetry
from app.bootstrap.settings import get_settings
from app.bootstrap.sweeper import Sweeper
from app.orchestration.assist import build_assist_graph
from app.orchestration.evidence import build_evidence_graph
from app.orchestration.resolution import build_resolution_graph

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI, *, start_sweeper: bool = True) -> AsyncIterator[None]:
    settings = get_settings()
    c = await build_container(settings)
    c.telemetry = setup_telemetry(settings, getattr(app.state, "span_exporter", None))
    instrument_db(c.engine)
    sweeper: asyncio.Task[None] | None = None
    try:
        saver = AsyncPostgresSaver(c.checkpoint_pool)  # type: ignore[arg-type]
        if settings.checkpoint_auto_setup:
            await saver.setup()  # dev/test only; staging and prod run it as a deploy step
        evidence = build_evidence_graph().compile(name="evidence")  # inherits the parent's checkpointer per run
        c.assist_graph = build_assist_graph(evidence).compile(checkpointer=saver, name="assist")
        c.resolution_graph = build_resolution_graph(evidence).compile(checkpointer=saver, name="resolution")
        if settings.tools_enabled:
            c.graph_services = dataclasses.replace(c.graph_services, tools=await connect_tools(c))
        app.state.container = c
        if start_sweeper and settings.sweeper_enabled:
            sweeper = asyncio.create_task(Sweeper(c).loop(settings.sweeper_interval_s), name="sweeper")
        log.info(
            "startup",
            env=settings.env,
            model_provider=settings.model_provider,
            tools_enabled=settings.tools_enabled,
            sweeper=sweeper is not None,
        )
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        await close_container(c)
        log.info("shutdown")
