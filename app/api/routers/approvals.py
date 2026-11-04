"""/v2 approvals (stage 11): role-filtered queue, detail, and the single-use decision that resumes the thread."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AppContainer, Principal, bounded_run, graph_context, require_role, resolution_runner
from app.api.schemas_v2 import ApprovalOut, DecisionIn, DecisionOut, ResolutionOut
from app.application.approvals import resume_value
from app.application.errors import ThreadBusy
from app.orchestration.context import Ctx
from app.orchestration.runners import ResolutionRunner

router = APIRouter(prefix="/v2/approvals", tags=["approvals"])
Analyst = Annotated[Principal, Depends(require_role("analyst"))]


@router.get("", response_model=list[ApprovalOut])
async def queue(p: Analyst, c: AppContainer, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> list[ApprovalOut]:
    items = await c.approval_service().queue(tenant_id=p.tenant_id, user_id=p.user_id, role=p.role, limit=limit)
    return [ApprovalOut.of(r) for r in items]


@router.get("/{approval_id}", response_model=ApprovalOut)
async def detail(approval_id: uuid.UUID, p: Analyst, c: AppContainer) -> ApprovalOut:
    return ApprovalOut.of(await c.approval_service().get(p.tenant_id, approval_id))


@router.post("/{approval_id}/decision", response_model=DecisionOut)
async def decide(
    approval_id: uuid.UUID,
    body: DecisionIn,
    p: Analyst,
    c: AppContainer,
    ctx: Annotated[Ctx, Depends(graph_context)],
    runner: Annotated[ResolutionRunner, Depends(resolution_runner)],
) -> DecisionOut:
    """1) decide in one DB transaction (role, four-eyes, payload hash, CAS, audit); 2) resume the thread under the
    approver's identity with the stored decision. If (2) fails the decision stands and the thread is marked
    resume_failed for the admin recover endpoint — the decision is never asked twice."""
    rec = await c.approval_service().decide(
        tenant_id=p.tenant_id,
        approval_id=approval_id,
        user_id=p.user_id,
        role=p.role,
        decision=body.decision,
        note=body.note,
        seen_summary_hash=body.summary_hash,
        request_id=ctx.request_id,
    )
    try:
        view = await bounded_run(runner.resume(rec.thread_id, resume_value(rec), ctx), c)
    except ThreadBusy:
        raise  # another runner is resuming it right now; the stored decision drives that run
    except BaseException:
        await ctx.services.threads.set_status(rec.thread_id, "resume_failed")
        raise
    return DecisionOut(approval=ApprovalOut.of(rec), resolution=ResolutionOut.of(view))
