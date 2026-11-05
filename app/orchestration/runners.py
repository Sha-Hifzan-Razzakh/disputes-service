"""Invokes compiled graphs: input, thread config, run limits, durability and the read model the API returns.

The runners live in orchestration (not application) because they need `Ctx`, graph config and `Command`; the API
calls them and wraps them in the run timeout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.application.errors import RunAborted, SideEffectUnsettled, ThreadBusy
from app.application.ports import CaseSnapshot
from app.domain.errors import InvalidTransition, NotFound
from app.orchestration.context import Ctx


@dataclass(frozen=True, slots=True)
class RunLimits:
    recursion_limit: int = 40
    max_concurrency: int = 4
    resume_lock_wait_s: float = 5.0  # a decision can arrive while the start run is still writing its interrupt


def run_config(thread_id: str, limits: RunLimits, *, ctx: Ctx) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": limits.recursion_limit,
        "max_concurrency": limits.max_concurrency,
        "metadata": {"tenant_id": ctx.tenant_id, "request_id": ctx.request_id, "user_id": ctx.user_id},
        "run_name": thread_id.split(":", 1)[0],
        "tags": [thread_id.split(":", 1)[0], f"tenant:{ctx.tenant_id}"],
    }


Graph = CompiledStateGraph[Any, Any, Any, Any]


@dataclass
class AssistRunner:
    graph: Graph
    limits: RunLimits

    async def ask(self, *, case: CaseSnapshot, question: str, ctx: Ctx) -> dict[str, Any]:
        d, txn = case.dispute, case.transaction
        state: dict[str, Any] = {
            "dispute": {
                "id": str(d.id),
                "network": d.network.value,
                "reason_code": d.reason_code,
                "amount": str(d.amount.amount),
                "currency": d.amount.currency,
                "status": d.status.value,
                "respond_by": d.respond_by.isoformat(),
            },
            "transaction": {
                "ref": txn.ref,
                "amount": str(txn.amount.amount),
                "three_ds_authenticated": txn.three_ds_authenticated,
                "avs_result": txn.avs_result,
                "cvv_result": txn.cvv_result,
                "delivery_status": txn.delivery_status,
                "delivery_tracking": txn.delivery_tracking,
                "refunded": str(txn.refunded.amount),
            },
            "evidence_kinds": sorted({e.kind for e in case.evidence}),
            "question": question,
        }
        config = run_config(f"assist:{d.id}", self.limits, ctx=ctx)
        out = await self.graph.ainvoke(state, config, context=ctx)
        return {
            "recommendation": out["recommendation"],
            "suggested_resolution": out["suggested_resolution"],
            "citations": out["citations"],
            "turns": len(out.get("history", [])),
        }


@dataclass
class ResolutionRunner:
    """Start, inspect and resume resolution threads. Durability is "sync": each super-step's checkpoint is written
    before the next starts, so a crash never loses a completed side-effect step (G5/G6)."""

    graph: Graph
    limits: RunLimits

    async def start(self, *, dispute_id: uuid.UUID, instructions: str, ctx: Ctx) -> dict[str, Any]:
        s = ctx.services
        snap = await s.cases.snapshot(ctx.tenant_id, dispute_id)  # NotFound for other tenants (404)
        if not snap.dispute.can_start_resolution:
            raise InvalidTransition(snap.dispute.status, "start_resolution")
        if unsettled := await s.side_effects.open_for(ctx.tenant_id, dispute_id):
            raise SideEffectUnsettled(str(dispute_id), [r.idempotency_key for r in unsettled])
        thread_id = await s.threads.start(
            tenant_id=ctx.tenant_id, dispute_id=dispute_id, graph="resolution", started_by=ctx.user_id
        )
        inp = {"dispute_id": str(dispute_id), "requested_by": ctx.user_id, "instructions": instructions}
        await self._invoke(inp, thread_id, ctx)
        return await self.view(thread_id, ctx, dispute_id=dispute_id)

    async def resume(self, thread_id: str, value: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
        """Continue a paused thread with a server-built resume value (approvals endpoint / sweeper, M3)."""
        await ctx.services.threads.get(ctx.tenant_id, thread_id)
        await self._invoke(Command(resume=value), thread_id, ctx, lock_wait_s=self.limits.resume_lock_wait_s)
        return await self.view(thread_id, ctx)

    async def recover(self, thread_id: str, ctx: Ctx) -> dict[str, Any]:
        """Continue a thread that failed mid-run from its last checkpoint (admin resume, M3). Completed super-steps
        are not re-run; the failed step runs again and is idempotent."""
        await ctx.services.threads.get(ctx.tenant_id, thread_id)
        await ctx.services.threads.set_status(thread_id, "running")
        await self._invoke(None, thread_id, ctx)
        return await self.view(thread_id, ctx)

    async def _invoke(self, inp: Any, thread_id: str, ctx: Ctx, *, lock_wait_s: float = 0.0) -> None:
        structlog.contextvars.bind_contextvars(thread_id=thread_id, tenant_id=ctx.tenant_id)
        try:
            async with ctx.services.threads.exclusive(thread_id, wait_s=lock_wait_s):  # one runner per thread
                await self.graph.ainvoke(
                    inp, run_config(thread_id, self.limits, ctx=ctx), context=ctx, durability="sync"
                )
        except ThreadBusy:
            raise  # the other runner owns the thread; its status stays as it is
        except GraphRecursionError as e:
            await ctx.services.threads.set_status(thread_id, "failed")
            raise RunAborted(thread_id, f"recursion limit {self.limits.recursion_limit} reached") from e
        except Exception:
            await ctx.services.threads.set_status(thread_id, "failed")
            raise

    async def history(self, thread_id: str, ctx: Ctx, *, limit: int) -> list[dict[str, Any]]:
        """Checkpoint trail for admins: step, writer node, next nodes, outcome. Values are not returned (SENSITIVE)."""
        await ctx.services.threads.get(ctx.tenant_id, thread_id)
        out: list[dict[str, Any]] = []
        async for snap in self.graph.aget_state_history(run_config(thread_id, self.limits, ctx=ctx), limit=limit):
            meta = snap.metadata or {}
            ran = [
                {"node": t.name, "wrote": sorted(t.result) if isinstance(t.result, dict) else []}
                for t in snap.tasks
                if t.result is not None
            ]
            out.append(
                {
                    "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                    "step": meta.get("step"),
                    "source": meta.get("source"),
                    "ran": ran,  # tasks executed from this checkpoint: node names and the keys they wrote, never values
                    "next": list(snap.next),
                    "created_at": snap.created_at,
                    "outcome": snap.values.get("outcome"),
                    "interrupted": bool(snap.interrupts),
                }
            )
        return out

    async def view(self, thread_id: str, ctx: Ctx, *, dispute_id: uuid.UUID | None = None) -> dict[str, Any]:
        rec = await ctx.services.threads.get(ctx.tenant_id, thread_id)  # tenant-filtered: other tenant → 404
        if dispute_id is not None and rec.dispute_id != dispute_id:
            raise NotFound("thread", thread_id)
        snap = await self.graph.aget_state(run_config(thread_id, self.limits, ctx=ctx))
        v = snap.values
        action = v.get("action") or {}
        approval = v.get("approval") or {}
        return {
            "thread_id": thread_id,
            "dispute_id": str(rec.dispute_id),
            "status": rec.status,
            "outcome": v.get("outcome") or ("AWAITING_APPROVAL" if snap.interrupts else None),
            "outcome_reason": v.get("outcome_reason"),
            "draft": v.get("draft"),
            "approval": {
                "approval_id": approval.get("approval_id"),
                "status": approval.get("status"),
                "required_role": action.get("required_role"),
                "priority": action.get("priority"),
                "expires_at": action.get("expires_at"),
                "decided_by": approval.get("decided_by"),
            }
            if approval
            else None,
            "execution": v.get("execution"),
            "next": list(snap.next),
        }
