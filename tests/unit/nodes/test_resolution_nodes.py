"""Every resolution node against its contract row (T14.3): guard → port → map → return, with in-memory fakes."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.application.case import case_to_state
from app.orchestration import resolution as R
from app.orchestration.evidence import fan_out, gather_txn_evidence, grade_evidence, plan_queries
from app.orchestration.state import OWNERS
from tests.fakes import NOW, FakeToolGateway, make_ctx, make_runtime, make_services

TID = "resolution:t:1"
NETWORK_RULE = {
    "id": "policy:n1",
    "source": "policy",
    "scope": "network",
    "kind": "network_rule",
    "title": "t",
    "excerpt": "x",
    "score": 0.5,
}


def setup(**case: Any) -> tuple[Any, Any, dict[str, Any]]:
    gs = make_services()
    did = gs.cases.add(**case)
    gs.threads.register(TID, dispute_id=did)
    snap = gs.cases.cases[("northwind", did)]
    state: dict[str, Any] = {
        "dispute_id": str(did),
        "requested_by": "ana",
        "instructions": "",
        "case": case_to_state(snap),
        "schema_version": 1,
    }
    return gs, make_runtime(make_ctx(gs), TID), state


def owned(node: str, update: dict[str, Any]) -> None:
    assert set(update) <= OWNERS["resolution"][node], f"{node} wrote {set(update) - OWNERS['resolution'][node]}"


async def test_load_case_routes_on_the_deadline() -> None:
    gs, rt, st = setup()
    inp = {"dispute_id": st["dispute_id"], "requested_by": "ana"}
    cmd = await R.load_case(inp, rt)
    assert cmd.goto == "classify_reason" and cmd.update["schema_version"] == 1
    gs.clock.advance(days=21)
    assert (await R.load_case(inp, rt)).goto == "close_expired"
    assert [r["action"] for r in gs.audit.rows] == ["resolution.started"]  # replay deduplicated


async def test_close_expired_advances_and_sets_outcome() -> None:
    gs, rt, st = setup()
    out = await R.close_expired(st, rt)
    owned("close_expired", out)
    assert out["outcome"] == "DEADLINE_PASSED" and gs.disputes.calls[-1][1] == "EXPIRED"


def test_classify_and_evidence_input() -> None:
    _, _, st = setup(code="10.4")
    st |= R.classify_reason(st)
    assert st["reason_family"] == "FRAUD"
    sub = R.evidence_input(st | {"instructions": "note"})
    assert sub["question"] == "note" and set(sub["facts"]) == {"transaction", "evidence"}


async def test_evidence_nodes_plan_fan_out_gather_and_grade() -> None:
    _gs, rt, st = setup(code="10.4", three_ds=True)
    sub = R.evidence_input(st | {"reason_family": "FRAUD"})
    sub |= plan_queries(sub)
    assert sub["retrieval_attempts"] == 1 and len(sub["queries"]) == 2
    sends = fan_out(sub)
    assert [getattr(s, "node", s) for s in sends] == ["retrieve_policy", "retrieve_policy", "gather_txn_evidence"]
    ev = gather_txn_evidence(sub)["evidence"]
    assert {e["id"] for e in ev} >= {"ledger:three_ds", "ledger:avs_cvv"} and ev[0]["kind"] == "three_ds_result"
    g = grade_evidence(sub | {"evidence": ev}, rt)["evidence_grade"]
    assert not g["sufficient"] and g["missing"] == ["network_rule"] and g["can_retry"]
    assert plan_queries(sub)["retrieval_attempts"] == 2  # round 2 broadens


def test_escalations_explain_why() -> None:
    out = R.escalate_insufficient({"retrieval_attempts": 2, "evidence_grade": {"missing": ["network_rule"]}})
    owned("escalate_insufficient", out)
    assert "missing: network_rule" in out["outcome_reason"]
    out = R.escalate_unverified({"verification": {"violations": ["R1: x", "R4: y"]}})
    assert out == {"outcome": "ABSTAINED_FAILED_VERIFICATION", "outcome_reason": "R1: x; R4: y"}


async def _drafted(**case: Any) -> tuple[Any, Any, dict[str, Any]]:
    gs, rt, st = setup(**case)
    st |= R.classify_reason(st)
    from app.application.evidence import ledger_evidence

    st["evidence"] = [
        *ledger_evidence(st["case"]["transaction"]),
        {
            "id": "policy:n1",
            "source": "policy",
            "scope": "network",
            "kind": "network_rule",
            "title": "t",
            "excerpt": "x",
            "score": 0.5,
        },
    ]
    out = await R.draft_resolution(st, rt)
    owned("draft_resolution", out)
    return gs, rt, st | out


async def test_draft_then_verify_passes_and_refine_counter_is_bounded() -> None:
    _gs, rt, st = await _drafted()
    assert st["draft"]["resolution"] == "ACCEPT" and st["draft"]["amount"] == "89.50"
    v = R.verify(st, rt)
    owned("verify", v)
    assert v["verification"] == {"passed": True, "violations": [], "can_refine": False}
    bad = st | {"draft": st["draft"] | {"amount": "999.00"}}
    v1 = R.verify(bad, rt)
    assert v1["refine_attempts"] == 1 and v1["verification"]["can_refine"]
    v2 = R.verify(bad | v1, rt)
    assert not v2["verification"]["can_refine"] and "refine_attempts" not in v2


async def test_prepare_action_fixes_key_role_and_expiry() -> None:
    _gs, rt, st = await _drafted(amount="640.00", code="10.4", avs="N", cvv="N")
    a = (await R.prepare_action(st, rt))["action"]
    assert (a["kind"], a["tool"], a["required_role"], a["priority"]) == (
        "refund",
        "payments.issue_refund",
        "ops_lead",
        "normal",
    )
    assert a["idempotency_key"] == (await R.prepare_action(st, rt))["action"]["idempotency_key"]  # stable
    assert a["expires_at"] == (NOW + timedelta(hours=72)).isoformat()
    info = st | {"draft": st["draft"] | {"resolution": "REQUEST_INFO", "amount": None}}
    assert (await R.prepare_action(info, rt))["action"]["kind"] == "request_info"


async def test_request_approval_is_idempotent_and_auto_is_recorded() -> None:
    gs, rt, st = await _drafted()
    st |= await R.prepare_action(st, rt)
    first = await R.request_approval(st, rt)
    again = await R.request_approval(st, rt)
    owned("request_approval", first)
    assert first == again and len(gs.approvals.rows) == 1 and gs.threads.rows[TID].status == "awaiting_approval"
    st2 = st | {"action": st["action"] | {"auto": True}}
    gs2, rt2, _ = setup()
    auto = await R.request_approval(st2, rt2)
    assert auto["approval"]["status"] == "auto" and next(iter(gs2.approvals.rows.values())).status == "auto"


async def _approved(**case: Any) -> tuple[Any, Any, dict[str, Any]]:
    gs, rt, st = await _drafted(**case)
    st |= await R.prepare_action(st, rt)
    st |= await R.request_approval(st, rt)
    st["approval"] |= {"status": "approved", "decided_by": "ben"}
    return gs, rt, st


@pytest.mark.parametrize(
    ("tool", "status", "outcome_node"),
    [
        (FakeToolGateway(), "succeeded", "apply_resolution"),
        (FakeToolGateway(permanent_error="closed"), "failed", None),
        (FakeToolGateway(transient_failures=1), "unknown", None),
        (FakeToolGateway(malformed=True), "unknown", None),
    ],
)
async def test_execute_maps_tool_results(tool: FakeToolGateway, status: str, outcome_node: str | None) -> None:
    gs, rt, st = await _approved()
    import dataclasses

    rt = make_runtime(make_ctx(dataclasses.replace(gs, tools=tool)), TID)
    out = await R.execute(st, rt)
    owned("execute", out)
    assert out["execution"]["status"] == status
    assert next(iter(gs.side_effects.rows.values())).status == status


async def test_execute_replay_after_success_makes_no_call_and_deadline_guard() -> None:
    gs, rt, st = await _approved()
    await R.execute(st, rt)
    await R.execute(st, rt)
    assert len(gs.tools.calls) == 1
    gs.clock.advance(days=30)
    assert (await R.execute(st, rt))["execution"]["error"].startswith("R3")


async def test_terminals_and_notify() -> None:
    gs, rt, st = await _approved()
    st |= await R.execute(st, rt)
    done = await R.apply_resolution(st, rt)
    owned("apply_resolution", done)
    assert done["outcome"] == "REFUNDED" and gs.disputes.calls[-1][1:] == ("RESOLVED_ACCEPTED", "ben")
    assert R.record_rejection(st | {"approval": st["approval"] | {"note": None}})["outcome"] == "REJECTED"
    assert R.record_expiry(st)["outcome"] == "APPROVAL_EXPIRED"
    unk = R.record_failure(
        st | {"execution": {"status": "unknown", "attempts": 3, "error": "timeout", "external_ref": None}}
    )
    assert "reconcile idempotency key" in unk["outcome_reason"]
    await R.notify(st | done, rt)
    await R.notify(st | done, rt)
    assert (
        len(gs.outbox.rows) == 1
        and gs.threads.rows[TID].status == "done"
        and gs.threads.rows[TID].outcome == "REFUNDED"
    )


async def test_request_info_moves_dispute_back() -> None:
    gs, rt, st = await _drafted(code="13.3")
    assert st["draft"]["resolution"] == "REQUEST_INFO"
    out = await R.request_info(st, rt)
    assert out["outcome"] == "INFO_REQUESTED" and gs.disputes.calls[-1][1] == "EVIDENCE_GATHERING"


async def test_load_case_for_another_tenant_is_not_found() -> None:
    from app.domain.errors import NotFound
    from tests.fakes import make_ctx as ctx_for

    gs, _, st = setup()
    with pytest.raises(NotFound):
        await R.load_case(
            {"dispute_id": st["dispute_id"], "requested_by": "zoe"}, make_runtime(ctx_for(gs, tenant="aurora"))
        )
    assert uuid.UUID(st["dispute_id"])
