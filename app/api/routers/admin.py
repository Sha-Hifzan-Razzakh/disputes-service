"""Admin-only thread inspection (T10.13). Thread ids resolve only through the tenant-filtered threads table."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import Principal, graph_context, require_role, resolution_runner
from app.orchestration.context import Ctx
from app.orchestration.runners import ResolutionRunner

router = APIRouter(prefix="/admin/threads", tags=["admin"])


@router.get("/{thread_id}/history")
async def thread_history(
    thread_id: str,
    p: Annotated[Principal, Depends(require_role("admin"))],
    runner: Annotated[ResolutionRunner, Depends(resolution_runner)],
    ctx: Annotated[Ctx, Depends(graph_context)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    items = await runner.history(thread_id, ctx, limit=limit)
    await ctx.services.audit.record(
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="admin.history_viewed",
        subject=f"thread:{thread_id}",
        request_id=ctx.request_id,
        data={"limit": limit},
    )
    return {"thread_id": thread_id, "checkpoints": items}
