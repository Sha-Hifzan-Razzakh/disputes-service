"""v1 dispute assistant graph: classify → retrieve policy → draft recommendation.

One thread per dispute (thread_id = "assist:<dispute_id>"); each question appends to `history`.
"""

from __future__ import annotations

import operator
from dataclasses import asdict
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.bootstrap.container import get_container
from app.domain.reason_codes import lookup


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


async def retrieve_policy(state: AssistState) -> dict[str, Any]:
    c = get_container()
    d = state["dispute"]
    query = f"{d['network']} {d['reason_code']} {state['reason_family']} {state['question']}"
    hits = await c.policy_store.search(tenant_id=state["tenant_id"], query=query, k=c.settings.retrieval_top_k)
    return {"policy_hits": [asdict(h) for h in hits]}


async def draft_recommendation(state: AssistState) -> dict[str, Any]:
    from app.application.ports import PolicyHit

    c = get_container()
    hits = [PolicyHit(**h) for h in state.get("policy_hits", [])]
    case = {
        "dispute": state["dispute"],
        "transaction": state["transaction"],
        "evidence_kinds": state.get("evidence_kinds", []),
        "reason_family": state["reason_family"],
    }
    rec = await c.drafter.draft(case=case, question=state["question"], hits=hits)
    return {
        "recommendation": rec.text,
        "suggested_resolution": rec.suggested_resolution,
        "citations": rec.citations,
        "history": [{"question": state["question"], "answer": rec.text}],
    }


def build_assist_graph() -> StateGraph:
    g = StateGraph(AssistState)
    g.add_node("classify_reason", classify_reason)
    g.add_node("retrieve_policy", retrieve_policy)
    g.add_node("draft_recommendation", draft_recommendation)
    g.add_edge(START, "classify_reason")
    g.add_edge("classify_reason", "retrieve_policy")
    g.add_edge("retrieve_policy", "draft_recommendation")
    g.add_edge("draft_recommendation", END)
    return g
