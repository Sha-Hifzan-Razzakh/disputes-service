"""v1 dispute assistant graph: classify → retrieve policy → draft recommendation.

One thread per dispute (thread_id = "assist:<dispute_id>"); each question appends to `history`.
Nodes reach services and the tenant through `runtime.context` (Ctx); the state shape is unchanged from v1.4.0 so
existing checkpoints keep loading. `tenant_id` is still accepted as input but no longer read.
"""

from __future__ import annotations

import operator
from dataclasses import asdict
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.application.ports import PolicyHit
from app.domain.reason_codes import lookup
from app.orchestration.context import Ctx


class AssistState(TypedDict, total=False):
    tenant_id: str
    dispute: dict[str, Any]
    transaction: dict[str, Any]
    evidence_kinds: list[str]
    question: str
    reason_family: str
    policy_hits: list[dict[str, Any]]
    recommendation: str
    suggested_resolution: str
    citations: list[str]
    history: Annotated[list[dict[str, str]], operator.add]


async def classify_reason(state: AssistState) -> dict[str, Any]:
    d = state["dispute"]
    return {"reason_family": lookup(d["network"], d["reason_code"]).family.value}


async def retrieve_policy(state: AssistState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    d = state["dispute"]
    query = f"{d['network']} {d['reason_code']} {state['reason_family']} {state['question']}"
    hits = await ctx.services.policy_store.search(tenant_id=ctx.tenant_id, query=query, k=ctx.services.retrieval_top_k)
    return {"policy_hits": [asdict(h) for h in hits]}


async def draft_recommendation(state: AssistState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    hits = [PolicyHit(**h) for h in state.get("policy_hits", [])]
    case: dict[str, object] = {
        "dispute": state["dispute"],
        "transaction": state["transaction"],
        "evidence_kinds": state.get("evidence_kinds", []),
        "reason_family": state["reason_family"],
    }
    rec = await runtime.context.services.drafter.draft(case=case, question=state["question"], hits=hits)
    return {
        "recommendation": rec.text,
        "suggested_resolution": rec.suggested_resolution,
        "citations": rec.citations,
        "history": [{"question": state["question"], "answer": rec.text}],
    }


def build_assist_graph() -> StateGraph[AssistState, Ctx, AssistState, AssistState]:
    g = StateGraph(AssistState, context_schema=Ctx)
    g.add_node("classify_reason", classify_reason)
    g.add_node("retrieve_policy", retrieve_policy)
    g.add_node("draft_recommendation", draft_recommendation)
    g.add_edge(START, "classify_reason")
    g.add_edge("classify_reason", "retrieve_policy")
    g.add_edge("retrieve_policy", "draft_recommendation")
    g.add_edge("draft_recommendation", END)
    return g
