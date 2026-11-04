"""Evidence subgraph, shared by the assist and resolution graphs (docs/graph_design.md: wrapped subgraph).

plan_queries ─┬─ Send(retrieve_policy) x N ─┐
              └─ gather_txn_evidence ───────┴─ grade_evidence ─┬─ sufficient → END
                                                               ├─ retry → plan_queries   (retrieval_attempts < max)
                                                               └─ exhausted → END
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, Send

from app.application.evidence import ledger_evidence, merchant_evidence
from app.application.ports import TransientError
from app.domain.reason_codes import ReasonFamily
from app.domain.rules import grade_evidence as grade
from app.orchestration.context import Ctx
from app.orchestration.routing import route_after_grade
from app.orchestration.state import EXCERPT_CHARS, Evidence, EvidenceState

READ_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.2, backoff_factor=2.0, retry_on=TransientError)

FAMILY_TERMS = {
    ReasonFamily.FRAUD: "card-absent fraud 3-D Secure AVS CVV liability",
    ReasonFamily.NOT_RECEIVED: "merchandise not received proof of delivery tracking",
    ReasonFamily.NOT_AS_DESCRIBED: "not as described defective partial refund restocking return policy",
    ReasonFamily.PROCESSING_ERROR: "duplicate processing refund already issued credit",
}


def plan_queries(state: EvidenceState) -> dict[str, Any]:
    """Round 1 keeps the v1 query (so assist answers stay stable) plus a family query; round 2 broadens."""
    attempt = state.get("retrieval_attempts", 0) + 1
    fam = ReasonFamily(state["reason_family"])
    base = f"{state['network']} {state['reason_code']} {fam.value} {state.get('question', '')}".strip()
    if attempt == 1:
        queries = [base, f"{FAMILY_TERMS[fam]} merchant policy"]
    else:
        queries = [f"network rules {FAMILY_TERMS[fam]} compelling evidence", f"refund and dispute policy {fam.value}"]
    return {"queries": queries, "retrieval_attempts": attempt}


def fan_out(state: EvidenceState) -> list[Send | str]:
    return [Send("retrieve_policy", {"queries": [q]}) for q in state["queries"]] + ["gather_txn_evidence"]


async def retrieve_policy(state: EvidenceState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    hits = await ctx.services.policy_store.search(
        tenant_id=ctx.tenant_id, query=state["queries"][0], k=ctx.services.limits.retrieval_top_k
    )
    items: list[Evidence] = [
        {
            "id": f"policy:{h.chunk_id}",
            "source": "policy",
            "scope": h.scope,
            "kind": "merchant_policy" if h.scope == "tenant" else "network_rule",
            "title": h.document_title,
            "excerpt": h.text[:EXCERPT_CHARS],
            "score": h.score,
        }
        for h in hits
    ]
    return {"evidence": items}


def gather_txn_evidence(state: EvidenceState) -> dict[str, Any]:
    facts = state["facts"]
    return {"evidence": ledger_evidence(facts["transaction"]) + merchant_evidence(facts.get("evidence", []))}


def grade_evidence(state: EvidenceState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    lim = runtime.context.services.limits
    g = grade(ReasonFamily(state["reason_family"]), state.get("evidence", []), min_score=lim.min_evidence_score)
    return {
        "evidence_grade": {
            "sufficient": g.sufficient,
            "missing": list(g.missing),
            "best_score": g.best_score,
            "can_retry": state["retrieval_attempts"] < lim.max_retrieval_attempts,
        }
    }


def build_evidence_graph() -> StateGraph[EvidenceState, Ctx, EvidenceState, EvidenceState]:
    g = StateGraph(EvidenceState, context_schema=Ctx)
    g.add_node("plan_queries", plan_queries)
    g.add_node("retrieve_policy", retrieve_policy, retry_policy=READ_RETRY)
    g.add_node("gather_txn_evidence", gather_txn_evidence)
    g.add_node("grade_evidence", grade_evidence)
    g.add_edge(START, "plan_queries")
    g.add_conditional_edges("plan_queries", fan_out, ["retrieve_policy", "gather_txn_evidence"])
    g.add_edge("retrieve_policy", "grade_evidence")
    g.add_edge("gather_txn_evidence", "grade_evidence")
    g.add_conditional_edges(
        "grade_evidence", route_after_grade, {"sufficient": END, "retry": "plan_queries", "exhausted": END}
    )
    return g


EvidenceInput = Callable[[dict[str, Any]], dict[str, Any]]


def wrapped_evidence_node(
    sub: CompiledStateGraph[Any, Any, Any, Any], to_input: EvidenceInput
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Parent node that maps parent state → EvidenceState, runs the subgraph (checkpointed under the parent
    thread's namespace through the propagated config) and copies the results back."""

    async def gather_evidence(state: dict[str, Any], runtime: Runtime[Ctx]) -> dict[str, Any]:
        out = await sub.ainvoke(to_input(state), context=runtime.context)
        return {k: out[k] for k in ("queries", "retrieval_attempts", "evidence", "evidence_grade")}

    return gather_evidence
