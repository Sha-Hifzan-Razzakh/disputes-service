"""Assist graph v2: upgrade_state → classify_reason → gather_evidence (shared subgraph) → draft_recommendation.

Same thread ids as v1 (`assist:<dispute_id>`) and the same `history` reducer, so v1 threads keep their turns
(ADR-0006). `upgrade_state` stamps schema_version 2 on threads written by v1.4.0; the legacy `tenant_id` and
`policy_hits` keys may still be present in old checkpoints and are never read.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.application.ports import PolicyHit
from app.domain.reason_codes import lookup
from app.orchestration.context import Ctx
from app.orchestration.evidence import wrapped_evidence_node
from app.orchestration.state import SCHEMA_VERSION, AssistStateV2

AssistState = AssistStateV2


def upgrade_state(state: AssistState) -> dict[str, Any]:
    """Checkpoint upgrade hook (stage 10). v1 threads have no schema_version: treat as 1 and stamp 2."""
    if state.get("schema_version", 1) < SCHEMA_VERSION["assist"]:
        return {"schema_version": SCHEMA_VERSION["assist"]}
    return {}


def classify_reason(state: AssistState) -> dict[str, Any]:
    d = state["dispute"]
    return {"reason_family": lookup(d["network"], d["reason_code"]).family.value}


def evidence_input(state: dict[str, Any]) -> dict[str, Any]:
    d = state["dispute"]
    return {
        "network": d["network"],
        "reason_code": d["reason_code"],
        "reason_family": state["reason_family"],
        "question": state["question"],
        "facts": {"transaction": state["transaction"], "evidence": []},
    }


async def draft_recommendation(state: AssistState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    hits = [
        PolicyHit(
            chunk_id=e["id"].removeprefix("policy:"),
            document_title=e["title"],
            text=e["excerpt"],
            score=e["score"],
            scope=e["scope"] or "network",
        )
        for e in state.get("evidence", [])
        if e["source"] == "policy"
    ]
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


def build_assist_graph(
    evidence: CompiledStateGraph[Any, Any, Any, Any],
) -> StateGraph[AssistState, Ctx, AssistState, AssistState]:
    g = StateGraph(AssistState, context_schema=Ctx)
    g.add_node("upgrade_state", upgrade_state)
    g.add_node("classify_reason", classify_reason)
    g.add_node("gather_evidence", wrapped_evidence_node(evidence, evidence_input))
    g.add_node("draft_recommendation", draft_recommendation)
    g.add_edge(START, "upgrade_state")
    g.add_edge("upgrade_state", "classify_reason")
    g.add_edge("classify_reason", "gather_evidence")
    g.add_edge("gather_evidence", "draft_recommendation")
    g.add_edge("draft_recommendation", END)
    return g
