"""Resolution graph (spec D1-D8, outcomes O1-O11; topology in docs/graph_design.md).

Nodes coordinate; rules live in `app.domain` and I/O behind the ports in `Ctx.services`. Every node that writes
outside the graph is idempotent on replay (path_to no-ops, ON CONFLICT DO NOTHING, idempotency keys), so a resumed
or retried super-step never repeats a business effect.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, RetryPolicy, interrupt

from app.application.case import case_to_state
from app.application.ports import ApprovalRecord, SideEffectRecord, TransientError
from app.domain import approval_policy
from app.domain.money import Money
from app.domain.reason_codes import Resolution, lookup
from app.domain.rules import verify_draft
from app.orchestration.context import Ctx
from app.orchestration.evidence import wrapped_evidence_node
from app.orchestration.instrument import instrument
from app.orchestration.routing import route_after_evidence, route_after_execute, route_after_verify, route_approval
from app.orchestration.state import SCHEMA_VERSION, ResolutionIn, ResolutionOut, ResolutionState

GRAPH = "resolution"
SYSTEM_ACTOR = "system:resolution"
APPROVAL_NS = uuid.UUID("6f0b7a52-4d0e-4b8e-9a39-3c1d2f7c9e11")
RETRY = RetryPolicy(max_attempts=3, initial_interval=0.2, backoff_factor=2.0, retry_on=TransientError)
TOOLS = {"refund": "payments.issue_refund", "representment": "network.submit_representment"}
OUTCOME_BY_RESOLUTION = {"ACCEPT": "REFUNDED", "PARTIAL": "PARTIALLY_REFUNDED", "CONTEST": "REPRESENTED"}


class InvalidResume(ValueError):
    pass


def _thread_id(runtime: Runtime[Ctx]) -> str:
    tid = runtime.execution_info.thread_id if runtime.execution_info else None
    if not tid:
        raise RuntimeError("resolution graph must run with a checkpointer and a thread_id")
    return tid


def _money(amount: str | None, currency: str) -> Money | None:
    return Money.of(amount, currency) if amount is not None else None


def _done(outcome: str, reason: str) -> dict[str, Any]:
    return {"outcome": outcome, "outcome_reason": reason}


# --- entry -------------------------------------------------------------------------------------------------------


async def load_case(
    state: ResolutionState, runtime: Runtime[Ctx]
) -> Command[Literal["close_expired", "classify_reason"]]:
    """D1 needs the clock, so the branch is a Command from the node rather than a router."""
    s, ctx = runtime.context.services, runtime.context
    snap = await s.cases.snapshot(ctx.tenant_id, uuid.UUID(state["dispute_id"]))
    expired = s.clock.now() >= snap.dispute.respond_by
    return Command(
        goto="close_expired" if expired else "classify_reason",
        update={"case": case_to_state(snap), "schema_version": SCHEMA_VERSION[GRAPH]},
    )


async def close_expired(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    await ctx.services.disputes.advance(
        tenant_id=ctx.tenant_id,
        dispute_id=uuid.UUID(state["dispute_id"]),
        goal="EXPIRED",
        actor=SYSTEM_ACTOR,
        note="response deadline passed before a resolution was started",
    )
    return _done("DEADLINE_PASSED", f"respond_by {state['case']['dispute']['respond_by']} has passed")


def classify_reason(state: ResolutionState) -> dict[str, Any]:
    d = state["case"]["dispute"]
    return {"reason_family": lookup(d["network"], d["reason_code"]).family.value}


def evidence_input(state: dict[str, Any]) -> dict[str, Any]:
    d = state["case"]["dispute"]
    return {
        "network": d["network"],
        "reason_code": d["reason_code"],
        "reason_family": state["reason_family"],
        "question": state.get("instructions", ""),
        "facts": {"transaction": state["case"]["transaction"], "evidence": state["case"]["evidence"]},
    }


def escalate_insufficient(state: ResolutionState) -> dict[str, Any]:
    g = state["evidence_grade"]
    return _done(
        "ABSTAINED_INSUFFICIENT_EVIDENCE",
        f"not enough evidence after {state['retrieval_attempts']} retrieval rounds; missing: {', '.join(g['missing'])}",
    )


# --- draft and verify ------------------------------------------------------------------------------------------


async def draft_resolution(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    s, ctx = runtime.context.services, runtime.context
    policy = await s.tenant_policies.get(ctx.tenant_id)
    case = {**state["case"], "reason_family": state["reason_family"], "partial_pct": policy.partial_pct}
    feedback = state.get("verification", {}).get("violations", [])
    d = await s.resolution_drafter.draft(
        case=case,
        evidence=[dict(e) for e in state["evidence"]],
        feedback=list(feedback),
        instructions=state.get("instructions", ""),
    )
    return {
        "draft": {
            "resolution": d.resolution,
            "amount": str(d.amount.amount) if d.amount else None,
            "currency": state["case"]["dispute"]["currency"],
            "rationale": d.rationale,
            "citations": list(d.citations),
        }
    }


def verify(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    s = runtime.context.services
    c, draft = state["case"], state["draft"]
    d, t = c["dispute"], c["transaction"]
    cur = d["currency"]
    violations = verify_draft(
        resolution=draft["resolution"],
        amount=_money(draft["amount"], draft["currency"]),
        citations=draft["citations"],
        reason=lookup(d["network"], d["reason_code"]),
        disputed=Money.of(d["amount"], cur),
        transaction_amount=Money.of(t["amount"], cur),
        refunded=Money.of(t["refunded"], cur),
        respond_by=datetime.fromisoformat(d["respond_by"]),
        now=s.clock.now(),
        evidence=state["evidence"],
    )
    refines = state.get("refine_attempts", 0)
    can_refine = bool(violations) and refines < s.limits.max_refine_attempts
    update: dict[str, Any] = {
        "verification": {"passed": not violations, "violations": violations, "can_refine": can_refine}
    }
    if can_refine:
        update["refine_attempts"] = refines + 1
    return update


def escalate_unverified(state: ResolutionState) -> dict[str, Any]:
    return _done("ABSTAINED_FAILED_VERIFICATION", "; ".join(state["verification"]["violations"]))


# --- action and approval ---------------------------------------------------------------------------------------


async def prepare_action(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    s, ctx = runtime.context.services, runtime.context
    draft, d = state["draft"], state["case"]["dispute"]
    res = Resolution(draft["resolution"])
    if res is Resolution.REQUEST_INFO:
        return {
            "action": {
                "kind": "request_info",
                "tool": None,
                "amount": None,
                "currency": d["currency"],
                "idempotency_key": None,
                "required_role": None,
                "priority": "normal",
                "urgent": False,
                "auto": False,
                "expires_at": None,
            }
        }
    policy = await s.tenant_policies.get(ctx.tenant_id)
    amount = _money(draft["amount"], draft["currency"])
    r = approval_policy.route(
        resolution=res,
        amount=amount,
        threshold=policy.approval_threshold,
        auto_approve_below=policy.auto_approve_below,
        respond_by=datetime.fromisoformat(d["respond_by"]),
        now=s.clock.now(),
        urgent_window=s.limits.urgent_window,
        ttl=s.limits.approval_ttl,
        deadline_margin=s.limits.approval_deadline_margin,
    )
    kind = "representment" if res is Resolution.CONTEST else "refund"
    key = approval_policy.idempotency_key(
        tenant_id=ctx.tenant_id, dispute_id=d["id"], thread_id=_thread_id(runtime), kind=kind, amount=amount
    )
    return {
        "action": {
            "kind": kind,
            "tool": TOOLS[kind],
            "amount": draft["amount"],
            "currency": d["currency"],
            "idempotency_key": key,
            "required_role": r.required_role,
            "priority": r.priority,
            "urgent": r.urgent,
            "auto": r.auto,
            "expires_at": r.expires_at.isoformat(),
        }
    }


async def request_info(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    await ctx.services.disputes.advance(
        tenant_id=ctx.tenant_id,
        dispute_id=uuid.UUID(state["dispute_id"]),
        goal="EVIDENCE_GATHERING",
        actor=state["requested_by"],
        note=state["draft"]["rationale"],
    )
    return _done("INFO_REQUESTED", state["draft"]["rationale"])


async def request_approval(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    s, ctx = runtime.context.services, runtime.context
    a, draft, tid = state["action"], state["draft"], _thread_id(runtime)
    aid = uuid.uuid5(APPROVAL_NS, tid)  # stable across replays → insert is idempotent
    now = s.clock.now()
    summary: dict[str, object] = {k: draft[k] for k in ("resolution", "amount", "currency", "rationale", "citations")}
    auto = a["auto"]
    await s.approvals.create(
        ApprovalRecord(
            id=aid,
            tenant_id=ctx.tenant_id,
            dispute_id=uuid.UUID(state["dispute_id"]),
            thread_id=tid,
            requested_by=state["requested_by"],
            required_role=a["required_role"] or "analyst",
            priority=a["priority"],
            status="auto" if auto else "pending",
            summary=summary,
            created_at=now,
            expires_at=datetime.fromisoformat(a["expires_at"] or ""),
            decided_by="system:auto" if auto else None,
            decided_at=now if auto else None,
        )
    )
    if auto:
        await s.audit.record(
            tenant_id=ctx.tenant_id,
            actor="system:auto",
            action="approval.auto",
            subject=f"approval:{aid}",
            request_id=ctx.request_id,
            data={"amount": a["amount"]},
            dedupe_key=f"approval.decided:{aid}",
        )
        return {"approval": {"approval_id": str(aid), "status": "auto", "decided_by": "system:auto", "note": None}}
    await s.threads.set_status(tid, "awaiting_approval")
    await s.audit.record(
        tenant_id=ctx.tenant_id,
        actor=state["requested_by"],
        action="approval.requested",
        subject=f"approval:{aid}",
        request_id=ctx.request_id,
        data={"required_role": a["required_role"], "priority": a["priority"]},
        dedupe_key=f"approval.requested:{aid}",
    )
    return {"approval": {"approval_id": str(aid), "status": "pending", "decided_by": None, "note": None}}


async def approve(
    state: ResolutionState, runtime: Runtime[Ctx]
) -> Command[Literal["execute", "record_rejection", "record_expiry"]]:
    """Pause until the approvals endpoint (or the expiry sweeper) resumes with a server-built decision.
    Nothing before `interrupt()` may have side effects: the node re-runs from the top on resume."""
    ap, a, draft = state["approval"], state["action"], state["draft"]
    if ap["status"] == "auto":
        return Command(goto="execute")
    value = interrupt(
        {
            "v": 1,
            "approval_id": ap["approval_id"],
            "required_role": a["required_role"],
            "priority": a["priority"],
            "expires_at": a["expires_at"],
            "summary": {k: draft[k] for k in ("resolution", "amount", "currency", "rationale")},
        }
    )
    if not isinstance(value, dict) or value.get("v") != 1 or value.get("approval_id") != ap["approval_id"]:
        raise InvalidResume("resume value must be {'v': 1, 'approval_id': <this approval>, 'decision': …}")
    decision = value.get("decision")
    if decision not in ("approved", "rejected", "expired"):
        raise InvalidResume(f"unknown decision {decision!r}")
    if decision == "approved" and value.get("decided_by") == state["requested_by"]:
        raise InvalidResume("four-eyes: the requester cannot approve their own resolution")  # defence in depth (G7)
    await runtime.context.services.threads.set_status(_thread_id(runtime), "running")
    goto: Literal["execute", "record_rejection", "record_expiry"] = (
        "execute" if decision == "approved" else "record_rejection" if decision == "rejected" else "record_expiry"
    )
    return Command(
        goto=goto,
        update={
            "approval": {**ap, "status": decision, "decided_by": value.get("decided_by"), "note": value.get("note")}
        },
    )


# --- execution and terminals -----------------------------------------------------------------------------------


async def execute(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    """At most one side effect per idempotency key (G6): reserve → call (gateway retries transient errors with the
    same key) → finish. A replay after success reads the stored result and makes no call."""
    s, ctx = runtime.context.services, runtime.context
    a, draft, d, c = state["action"], state["draft"], state["case"]["dispute"], state["case"]
    key = a["idempotency_key"] or ""
    if s.clock.now() >= datetime.fromisoformat(d["respond_by"]):
        return {
            "execution": {
                "status": "failed",
                "external_ref": None,
                "attempts": 0,
                "error": "R3: response deadline passed before execution",
            }
        }
    rec = await s.side_effects.reserve(SideEffectRecord(key, ctx.tenant_id, uuid.UUID(d["id"]), a["kind"], "pending"))
    if rec.status == "succeeded":
        return {"execution": {"status": "succeeded", "external_ref": rec.external_ref, "attempts": 0, "error": None}}
    if a["kind"] == "refund":
        args: dict[str, object] = {
            "transaction_ref": c["transaction"]["ref"],
            "amount": a["amount"],
            "currency": a["currency"],
            "dispute_id": d["id"],
        }
    else:
        args = {
            "dispute_id": d["id"],
            "network": d["network"],
            "reason_code": d["reason_code"],
            "evidence_ids": draft["citations"],
            "rationale": draft["rationale"],
        }
    approval_id = state["approval"]["approval_id"]
    result = await s.tools.call(
        a["tool"] or "",
        args,
        tenant_id=ctx.tenant_id,
        idempotency_key=key,
        approval_id=None if approval_id == "auto" else uuid.UUID(approval_id),
    )
    ref = result.data.get("external_ref") if result.ok else None
    status = "succeeded" if result.ok else ("unknown" if result.retryable else "failed")
    await s.side_effects.finish(key, status=status, external_ref=str(ref) if ref else None, error=result.error)
    attempts = result.data.get("attempts", 1)
    return {
        "execution": {
            "status": status,
            "external_ref": str(ref) if ref else None,
            "attempts": attempts if isinstance(attempts, int) else 1,
            "error": result.error,
        }
    }


async def apply_resolution(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    ctx = runtime.context
    a, ex = state["action"], state["execution"]
    goal = "RESOLVED_CONTESTED" if a["kind"] == "representment" else "RESOLVED_ACCEPTED"
    await ctx.services.disputes.advance(
        tenant_id=ctx.tenant_id,
        dispute_id=uuid.UUID(state["dispute_id"]),
        goal=goal,
        actor=state["approval"]["decided_by"] or state["requested_by"],
        note=f"{a['kind']} {ex['external_ref']}",
    )
    return _done(OUTCOME_BY_RESOLUTION[state["draft"]["resolution"]], f"{a['kind']} {ex['external_ref']}")


def record_rejection(state: ResolutionState) -> dict[str, Any]:
    ap = state["approval"]
    return _done("REJECTED", f"rejected by {ap['decided_by']}: {ap['note'] or 'no note'}")


def record_expiry(state: ResolutionState) -> dict[str, Any]:
    return _done("APPROVAL_EXPIRED", f"approval not decided before {state['action']['expires_at']}")


def record_failure(state: ResolutionState) -> dict[str, Any]:
    ex = state["execution"]
    if ex["status"] == "unknown":
        reason = (
            f"outcome unknown after {ex['attempts']} attempts ({ex['error']}); reconcile idempotency key "
            f"{state['action']['idempotency_key']}"
        )
    else:
        reason = ex["error"] or "tool call failed"
    return _done("EXECUTION_FAILED", reason)


async def notify(state: ResolutionState, runtime: Runtime[Ctx]) -> dict[str, Any]:
    """Single exit: outbox message, audit row and thread status for every outcome (all deduplicated per thread)."""
    s, ctx, tid = runtime.context.services, runtime.context, _thread_id(runtime)
    payload: dict[str, object] = {
        "thread_id": tid,
        "dispute_id": state["dispute_id"],
        "outcome": state["outcome"],
        "reason": state.get("outcome_reason"),
        "resolution": state.get("draft", {}).get("resolution"),
    }
    await s.outbox.enqueue(
        tenant_id=ctx.tenant_id, type="dispute.resolution_completed", payload=payload, dedupe_key=f"{tid}:completed"
    )
    await s.audit.record(
        tenant_id=ctx.tenant_id,
        actor=ctx.user_id,
        action="resolution.completed",
        subject=f"thread:{tid}",
        request_id=ctx.request_id,
        data={"outcome": state["outcome"]},
        dedupe_key=f"resolution.completed:{tid}",
    )
    await s.threads.set_status(tid, "done", outcome=state["outcome"])
    return {}


TERMINALS = (
    "close_expired",
    "escalate_insufficient",
    "escalate_unverified",
    "request_info",
    "apply_resolution",
    "record_rejection",
    "record_expiry",
    "record_failure",
)


def build_resolution_graph(
    evidence: CompiledStateGraph[Any, Any, Any, Any],
) -> StateGraph[ResolutionState, Ctx, ResolutionIn, ResolutionOut]:
    g = StateGraph(ResolutionState, context_schema=Ctx, input_schema=ResolutionIn, output_schema=ResolutionOut)
    g.add_node("load_case", instrument("resolution", "load_case", load_case), retry_policy=RETRY)
    g.add_node("close_expired", instrument("resolution", "close_expired", close_expired), retry_policy=RETRY)
    g.add_node("classify_reason", instrument("resolution", "classify_reason", classify_reason))
    g.add_node(
        "gather_evidence", instrument("resolution", "gather_evidence", wrapped_evidence_node(evidence, evidence_input))
    )
    g.add_node("escalate_insufficient", instrument("resolution", "escalate_insufficient", escalate_insufficient))
    g.add_node("draft_resolution", instrument("resolution", "draft_resolution", draft_resolution), retry_policy=RETRY)
    g.add_node("verify", instrument("resolution", "verify", verify))
    g.add_node("escalate_unverified", instrument("resolution", "escalate_unverified", escalate_unverified))
    g.add_node("prepare_action", instrument("resolution", "prepare_action", prepare_action), retry_policy=RETRY)
    g.add_node("request_info", instrument("resolution", "request_info", request_info), retry_policy=RETRY)
    g.add_node("request_approval", instrument("resolution", "request_approval", request_approval), retry_policy=RETRY)
    g.add_node("approve", instrument("resolution", "approve", approve))
    g.add_node("execute", instrument("resolution", "execute", execute), retry_policy=RETRY)
    g.add_node("apply_resolution", instrument("resolution", "apply_resolution", apply_resolution), retry_policy=RETRY)
    g.add_node("record_rejection", instrument("resolution", "record_rejection", record_rejection))
    g.add_node("record_expiry", instrument("resolution", "record_expiry", record_expiry))
    g.add_node("record_failure", instrument("resolution", "record_failure", record_failure))
    g.add_node("notify", instrument("resolution", "notify", notify), retry_policy=RETRY)

    g.add_edge(START, "load_case")
    g.add_edge("classify_reason", "gather_evidence")
    g.add_conditional_edges(
        "gather_evidence",
        route_after_evidence,
        {"sufficient": "draft_resolution", "insufficient": "escalate_insufficient"},
    )
    g.add_edge("draft_resolution", "verify")
    g.add_conditional_edges(
        "verify",
        route_after_verify,
        {"passed": "prepare_action", "refine": "draft_resolution", "failed": "escalate_unverified"},
    )
    g.add_conditional_edges(
        "prepare_action", route_approval, {"request_info": "request_info", "approval": "request_approval"}
    )
    g.add_edge("request_approval", "approve")
    g.add_conditional_edges(
        "execute", route_after_execute, {"succeeded": "apply_resolution", "failed": "record_failure"}
    )
    for t in TERMINALS:
        g.add_edge(t, "notify")
    g.add_edge("notify", END)
    return g
