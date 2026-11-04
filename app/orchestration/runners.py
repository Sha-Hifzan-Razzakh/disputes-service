"""Invokes compiled graphs: builds the input from a case snapshot, the thread config and the run limits.

The runner lives in orchestration (not application) because it needs `Ctx` and graph config; the API calls it and
wraps it in the run timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.application.ports import CaseSnapshot
from app.orchestration.context import Ctx


@dataclass(frozen=True, slots=True)
class RunLimits:
    recursion_limit: int = 40
    max_concurrency: int = 4


def run_config(thread_id: str, limits: RunLimits, *, ctx: Ctx) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": limits.recursion_limit,
        "max_concurrency": limits.max_concurrency,
        "metadata": {"tenant_id": ctx.tenant_id, "request_id": ctx.request_id, "user_id": ctx.user_id},
    }


@dataclass
class AssistRunner:
    graph: CompiledStateGraph[Any, Any, Any, Any]
    limits: RunLimits

    async def ask(self, *, case: CaseSnapshot, question: str, ctx: Ctx) -> dict[str, Any]:
        d, txn = case.dispute, case.transaction
        state: dict[str, Any] = {
            "tenant_id": ctx.tenant_id,  # legacy key kept for checkpoint shape; nodes read ctx.tenant_id
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
