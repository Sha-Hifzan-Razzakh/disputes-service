"""/v2 resolution workflow: start a resolution and read its state (approvals and streaming arrive in M3)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import AppContainer, Principal, bounded_run, graph_context, require_role, resolution_runner
from app.api.schemas_v2 import ResolutionOut, StartResolutionIn
from app.orchestration.context import Ctx
from app.orchestration.runners import ResolutionRunner

router = APIRouter(prefix="/v2/disputes/{dispute_id}/resolutions", tags=["resolutions"])

Runner = Annotated[ResolutionRunner, Depends(resolution_runner)]
Context = Annotated[Ctx, Depends(graph_context)]


@router.post(
    "",
    response_model=ResolutionOut,
    status_code=status.HTTP_201_CREATED,
    responses={202: {"model": ResolutionOut, "description": "Paused for approval"}},
)
async def start_resolution(
    dispute_id: uuid.UUID,
    body: StartResolutionIn,
    response: Response,
    _: Annotated[Principal, Depends(require_role("analyst"))],
    runner: Runner,
    ctx: Context,
    c: AppContainer,
) -> ResolutionOut:
    view = await bounded_run(runner.start(dispute_id=dispute_id, instructions=body.instructions, ctx=ctx), c)
    if view["status"] == "awaiting_approval":
        response.status_code = status.HTTP_202_ACCEPTED
    response.headers["Location"] = f"/v2/disputes/{dispute_id}/resolutions/{view['thread_id']}"
    return ResolutionOut.of(view)


@router.get("/{thread_id}", response_model=ResolutionOut)
async def get_resolution(
    dispute_id: uuid.UUID,
    thread_id: str,
    _: Annotated[Principal, Depends(require_role("analyst"))],
    runner: Runner,
    ctx: Context,
) -> ResolutionOut:
    return ResolutionOut.of(await runner.view(thread_id, ctx, dispute_id=dispute_id))


TERMINAL_THREAD = {"done", "failed", "cancelled"}


@router.get("/{thread_id}/stream")
async def stream_resolution(
    dispute_id: uuid.UUID,
    thread_id: str,
    _: Annotated[Principal, Depends(require_role("analyst"))],
    runner: Runner,
    ctx: Context,
    poll_s: float = 0.5,
    idle_s: float = 60.0,
) -> StreamingResponse:
    """Server-sent events: one `state` event per change (status, next nodes, outcome), closing when the thread
    reaches a terminal status or nothing changed for `idle_s` (pressure card: LIGHT/Scale)."""
    first = await runner.view(thread_id, ctx, dispute_id=dispute_id)  # 404 before the stream opens

    async def events() -> AsyncIterator[str]:
        last, view, idle = None, first, 0.0
        while True:
            snapshot = {k: view[k] for k in ("status", "next", "outcome", "outcome_reason")}
            if snapshot != last:
                yield f"event: state\ndata: {json.dumps(snapshot)}\n\n"
                last, idle = snapshot, 0.0
            if view["status"] in TERMINAL_THREAD or idle >= idle_s:
                yield "event: end\ndata: {}\n\n"
                return
            await asyncio.sleep(poll_s)
            idle += poll_s
            view = await runner.view(thread_id, ctx, dispute_id=dispute_id)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
