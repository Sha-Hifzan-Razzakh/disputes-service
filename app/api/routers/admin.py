"""Admin-only thread inspection (T10.13). Thread ids resolve only through the tenant-filtered threads table."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AppContainer, Principal, bounded_run, graph_context, require_role, resolution_runner
from app.api.schemas_v2 import ResolutionOut
from app.application.approvals import resume_value
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


RECOVERABLE = {"failed", "resume_failed", "running"}


@router.post("/{thread_id}/recover", response_model=ResolutionOut)
async def recover(
    thread_id: str,
    p: Annotated[Principal, Depends(require_role("admin"))],
    runner: Annotated[ResolutionRunner, Depends(resolution_runner)],
    ctx: Annotated[Ctx, Depends(graph_context)],
    c: AppContainer,
) -> ResolutionOut:
    """Finish a thread that failed mid-run, or replay a stored approval decision whose resume failed. Completed
    steps never re-run; side-effecting steps are idempotent."""
    rec = await ctx.services.threads.get(p.tenant_id, thread_id)
    view = await runner.view(thread_id, ctx)
    # a thread still "awaiting_approval" whose approval was already decided lost its resume: recover it too (R5)
    stranded = rec.status == "awaiting_approval" and view["next"] == ["approve"]
    if rec.status not in RECOVERABLE and not stranded:
        raise HTTPException(409, f"thread is {rec.status}; only {sorted(RECOVERABLE)} can be recovered")
    approval = view.get("approval") or {}
    await ctx.services.audit.record(
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="admin.thread_recovered",
        subject=f"thread:{thread_id}",
        request_id=ctx.request_id,
        data={"status": rec.status, "next": view["next"]},
    )
    if view["next"] == ["approve"] and approval.get("approval_id"):
        stored = await c.approval_service().get(p.tenant_id, uuid.UUID(approval["approval_id"]))
        if stored.status == "pending":
            raise HTTPException(409, "thread is waiting for a decision; use /v2/approvals/{id}/decision")
        await ctx.services.threads.set_status(thread_id, "running")
        return ResolutionOut.of(await bounded_run(runner.resume(thread_id, resume_value(stored), ctx), c))
    return ResolutionOut.of(await bounded_run(runner.recover(thread_id, ctx), c))


@router.post("/{thread_id}/cancel", status_code=204)
async def cancel(
    thread_id: str,
    p: Annotated[Principal, Depends(require_role("admin"))],
    ctx: Annotated[Ctx, Depends(graph_context)],
) -> None:
    """Stop a stuck thread so a new resolution can start. Business records stay; nothing external is undone."""
    rec = await ctx.services.threads.get(p.tenant_id, thread_id)
    if rec.status not in RECOVERABLE:
        raise HTTPException(409, f"thread is {rec.status}")
    await ctx.services.threads.set_status(thread_id, "cancelled")
    await ctx.services.audit.record(
        tenant_id=p.tenant_id,
        actor=p.user_id,
        action="admin.thread_cancelled",
        subject=f"thread:{thread_id}",
        request_id=ctx.request_id,
        data={"status": rec.status},
    )
