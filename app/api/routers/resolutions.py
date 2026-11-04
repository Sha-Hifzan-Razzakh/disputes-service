"""/v2 resolution workflow: start a resolution and read its state (approvals and streaming arrive in M3)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

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
