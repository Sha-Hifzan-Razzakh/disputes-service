"""Resolution graph scenarios against real Postgres (stages 8-10): outcomes, routing, retries, recovery."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest

from app.application.errors import RunAborted
from app.orchestration.resolution import InvalidResume
from app.orchestration.runners import RunLimits
from tests.conftest import BASE_DSN, TEST_DB
from tests.graph.fakes import TRANSIENT, FailOnce

Auth = Callable[..., dict[str, str]]


def sql(q: str, *args: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(f"{BASE_DSN}/{TEST_DB}", autocommit=True) as conn:
        cur = conn.execute(q, args)
        return cur.fetchall() if cur.description else []


async def dispute(
    client: httpx.AsyncClient,
    auth: Auth,
    ref: str,
    code: str,
    amount: str,
    *,
    network: str = "VISA",
    review: bool = True,
    tenant: str = "northwind",
    cur: str = "USD",
) -> str:
    h = auth(tenant=tenant)
    r = await client.post(
        "/v1/disputes",
        json={"network": network, "reason_code": code, "transaction_ref": ref, "amount": amount, "currency": cur},
        headers=h,
    )
    assert r.status_code == 201, r.text
    d = r.json()
    for i, action in enumerate(["start_evidence", "submit_for_review"][: 2 if review else 1], start=1):
        r = await client.patch(f"/v1/disputes/{d['id']}", json={"action": action, "expected_version": i}, headers=h)
        assert r.status_code == 200, r.text
    return str(d["id"])


async def start(client: httpx.AsyncClient, auth: Auth, did: str, *, user: str = "ana", **body: Any) -> httpx.Response:
    return await client.post(f"/v2/disputes/{did}/resolutions", json=body, headers=auth(user=user))


def services(app: Any, **changes: Any) -> None:
    c = app.state.container
    c.graph_services = dataclasses.replace(c.graph_services, **changes)


def ctx(app: Any, user: str = "ben", role: str = "analyst", tenant: str = "northwind") -> Any:
    return app.state.container.graph_context(tenant_id=tenant, user_id=user, role=role, request_id=f"test-{user}")


def decision(view: dict[str, Any], decision: str, by: str = "ben", note: str | None = None) -> dict[str, Any]:
    return {
        "v": 1,
        "approval_id": view["approval"]["approval_id"],
        "decision": decision,
        "decided_by": by,
        "note": note,
    }


# --- outcomes up to the first stop ---------------------------------------------------------------------------


async def test_o11_fraud_with_3ds_pauses_for_analyst_approval(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    r = await start(client, auth, did)
    assert r.status_code == 202, r.text
    v = r.json()
    assert v["status"] == "awaiting_approval" and v["outcome"] == "AWAITING_APPROVAL" and v["next"] == ["approve"]
    assert v["draft"]["resolution"] == "CONTEST" and "ledger:three_ds" in v["draft"]["citations"]
    assert (v["approval"]["required_role"], v["approval"]["priority"]) == ("analyst", "normal")
    assert r.headers["Location"].endswith(v["thread_id"])
    assert sql("SELECT status, requested_by FROM approvals WHERE thread_id = %s", v["thread_id"]) == [
        ("pending", "ana")
    ]


async def test_refund_at_or_above_threshold_needs_ops_lead(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1002", "10.4", "640.00")  # no 3DS, AVS/CVV mismatch → ACCEPT
    v = (await start(client, auth, did)).json()
    assert v["draft"]["resolution"] == "ACCEPT" and v["draft"]["amount"] == "640.00"
    assert v["approval"]["required_role"] == "ops_lead"  # northwind threshold 500.00


async def test_o4_request_info_moves_dispute_back_to_evidence_gathering(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")  # NAD, no merchant uploads
    r = await start(client, auth, did)
    v = r.json()
    assert r.status_code == 201 and v["outcome"] == "INFO_REQUESTED" and v["status"] == "done"
    assert "product_description" in v["outcome_reason"]
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "EVIDENCE_GATHERING"
    assert sql("SELECT count(*) FROM approvals WHERE dispute_id = %s", did) == [(0,)]


async def test_o5_retriever_that_finds_nothing_abstains_after_two_rounds(client, auth, tools, app, monkeypatch) -> None:
    store = app.state.container.graph_services.policy_store
    calls: list[str] = []

    async def nothing(*, tenant_id: str, query: str, k: int) -> list:
        calls.append(query)
        return []

    monkeypatch.setattr(store, "search", nothing)
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    assert v["outcome"] == "ABSTAINED_INSUFFICIENT_EVIDENCE" and "network_rule" in v["outcome_reason"]
    assert len(calls) == 4  # 2 rounds x 2 queries, then stop (bounded loop)


async def test_o6_overpaying_drafter_is_refined_once_then_abstains(client, auth, tools, app) -> None:
    from app.application.ports import DraftResolution
    from app.domain.money import Money

    class Overpays:
        runs = 0

        async def draft(self, **_: Any) -> DraftResolution:
            Overpays.runs += 1
            return DraftResolution("ACCEPT", Money.of("999.00", "USD"), "pay it all", ("ledger:three_ds",))

    original = app.state.container.graph_services
    services(app, resolution_drafter=Overpays())
    try:
        did = await dispute(client, auth, "NW-1002", "13.1", "640.00")
        v = (await start(client, auth, did)).json()
    finally:
        app.state.container.graph_services = original
    assert v["outcome"] == "ABSTAINED_FAILED_VERIFICATION" and v["outcome_reason"].startswith("R1")
    assert Overpays.runs == 2


async def test_o9_deadline_passed_expires_the_dispute(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1004", "4837", "1249.00", network="MASTERCARD")
    sql("UPDATE disputes SET respond_by = now() - interval '1 hour' WHERE id = %s", did)
    v = (await start(client, auth, did)).json()
    assert v["outcome"] == "DEADLINE_PASSED"
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "EXPIRED"
    assert tools.calls == []


async def test_urgent_dispute_routes_to_ops_lead(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    sql("UPDATE disputes SET respond_by = now() + interval '30 hours' WHERE id = %s", did)
    v = (await start(client, auth, did)).json()
    assert (v["approval"]["required_role"], v["approval"]["priority"]) == ("ops_lead", "urgent")


async def test_a1_second_start_while_awaiting_approval_is_409(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    assert (await start(client, auth, did)).status_code == 202
    r = await start(client, auth, did)
    assert r.status_code == 409 and r.json()["error"]["code"] == "resolution_in_progress"


async def test_a2_dispute_not_resolvable_is_409(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00", review=False)
    await client.post(f"/v1/disputes/{did}/withdraw?expected_version=2", headers=auth())
    r = await start(client, auth, did)
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"


async def test_a6_other_tenant_gets_404_for_dispute_and_thread(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    tid = (await start(client, auth, did)).json()["thread_id"]
    other = auth(tenant="aurora", role="admin")
    assert (await client.post(f"/v2/disputes/{did}/resolutions", json={}, headers=other)).status_code == 404
    assert (await client.get(f"/v2/disputes/{did}/resolutions/{tid}", headers=other)).status_code == 404


async def test_instructions_are_bounded(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    r = await start(client, auth, did, instructions="x" * 1001)
    assert r.status_code == 422


# --- approval decisions and execution (resume through the runner; API endpoints arrive in M3) -----------------


async def test_o1_approved_refund_executes_once_and_resolves(client, auth, tools, app) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")  # in transit → ACCEPT 89.50
    v = (await start(client, auth, did)).json()
    runner = app.state.container.resolution_runner()
    out = await runner.resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "REFUNDED" and out["status"] == "done" and out["execution"]["status"] == "succeeded"
    assert [c["tool"] for c in tools.calls] == ["payments.issue_refund"]
    assert tools.calls[0]["args"]["amount"] == "89.50"
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "RESOLVED_ACCEPTED"
    assert sql("SELECT type FROM outbox WHERE dedupe_key = %s", f"{v['thread_id']}:completed") == [
        ("dispute.resolution_completed",)
    ]
    actions = [
        a for (a,) in sql("SELECT action FROM audit_log WHERE subject LIKE %s ORDER BY id", f"%{v['thread_id']}%")
    ]
    assert actions == ["resolution.started", "resolution.completed"]


async def test_o3_approved_contest_submits_representment(client, auth, tools, app) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "REPRESENTED"
    assert tools.calls[0]["tool"] == "network.submit_representment"
    assert "ledger:three_ds" in tools.calls[0]["args"]["evidence_ids"]


async def test_o7_rejection_ends_without_side_effects(client, auth, tools, app) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(
        v["thread_id"], decision(v, "rejected", note="replacement shipped"), ctx(app)
    )
    assert out["outcome"] == "REJECTED" and "replacement shipped" in out["outcome_reason"] and tools.calls == []
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "UNDER_REVIEW"


async def test_o8_expiry_and_o10_permanent_failure(client, auth, tools, app) -> None:
    runner = app.state.container.resolution_runner()
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    assert (await runner.resume(v["thread_id"], decision(v, "expired", by="system:sweeper"), ctx(app)))[
        "outcome"
    ] == "APPROVAL_EXPIRED"

    tools.permanent_error = "card_account_closed"
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await runner.resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "EXECUTION_FAILED" and out["outcome_reason"] == "card_account_closed"
    assert sql("SELECT status FROM side_effects WHERE dispute_id = %s", did) == [("failed",)]
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "UNDER_REVIEW"


async def test_retries_exhausted_in_gateway_mark_outcome_unknown_for_reconciliation(client, auth, tools, app) -> None:
    tools.transient_failures = 1
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "EXECUTION_FAILED" and "reconcile idempotency key" in out["outcome_reason"]
    assert sql("SELECT status FROM side_effects WHERE dispute_id = %s", did) == [("unknown",)]


async def test_four_eyes_and_malformed_resume_are_refused_by_the_graph(client, auth, tools, app) -> None:
    runner = app.state.container.resolution_runner()
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    with pytest.raises(InvalidResume, match="four-eyes"):
        await runner.resume(v["thread_id"], decision(v, "approved", by="ana"), ctx(app, user="ana"))
    with pytest.raises(InvalidResume):
        await runner.resume(v["thread_id"], {"decision": "approved"}, ctx(app))
    assert tools.calls == []


# --- recovery (stage 10) ---------------------------------------------------------------------------------------


async def test_transient_failure_after_the_tool_call_retries_without_a_second_refund(client, auth, tools, app) -> None:
    """execute: the refund succeeds, then recording it fails transiently. RetryPolicy re-runs the node with the
    same idempotency key; the payments side dedupes, so there is exactly one refund and one side-effect row."""
    c = app.state.container
    original = c.graph_services
    services(app, side_effects=FailOnce(original.side_effects, "finish", TRANSIENT))
    try:
        did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
        v = (await start(client, auth, did)).json()
        out = await c.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    finally:
        c.graph_services = original
    assert out["outcome"] == "REFUNDED"
    assert len(tools.calls) == 2 and len(tools.effects) == 1
    assert sql("SELECT count(*), min(status) FROM side_effects WHERE dispute_id = %s", did) == [(1, "succeeded")]


async def test_crash_between_refund_and_dispute_update_recovers_without_repeating_the_refund(
    client, auth, tools, app
) -> None:
    c = app.state.container
    original = c.graph_services
    crash = RuntimeError("process died after refund")
    services(app, disputes=FailOnce(original.disputes, "advance", crash, when={"goal": "RESOLVED_ACCEPTED"}))
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    with pytest.raises(RuntimeError, match="process died"):
        await c.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert sql("SELECT status FROM threads WHERE thread_id = %s", v["thread_id"]) == [("failed",)]
    c.graph_services = original  # "restart"
    out = await c.resolution_runner().recover(v["thread_id"], ctx(app))
    assert out["outcome"] == "REFUNDED" and out["status"] == "done"
    assert len(tools.calls) == 1  # execute's checkpoint was durable; only apply_resolution re-ran
    assert (await client.get(f"/v1/disputes/{did}", headers=auth())).json()["status"] == "RESOLVED_ACCEPTED"


async def test_tools_kill_switch_ends_in_execution_failed(client, auth, app) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "EXECUTION_FAILED" and "tools disabled" in out["outcome_reason"]


async def test_worst_path_fits_the_recursion_budget(client, auth, tools, app) -> None:
    """O6 is the longest single invocation (two drafts, two verifies): 9 node super-steps + the input step = 10."""
    from app.application.ports import DraftResolution
    from app.domain.money import Money

    class Overpays:
        async def draft(self, **_: Any) -> DraftResolution:
            return DraftResolution("ACCEPT", Money.of("999.00", "USD"), "x", ("ledger:three_ds",))

    c = app.state.container
    original, limits = c.graph_services, c.limits
    services(app, resolution_drafter=Overpays())
    try:
        c.limits = RunLimits(recursion_limit=10)
        ok = await c.resolution_runner().start(
            dispute_id=uuid.UUID(await dispute(client, auth, "NW-1002", "13.1", "640.00")),
            instructions="",
            ctx=ctx(app, user="ana"),
        )
        assert ok["outcome"] == "ABSTAINED_FAILED_VERIFICATION"
        c.limits = RunLimits(recursion_limit=9)
        with pytest.raises(RunAborted):
            await c.resolution_runner().start(
                dispute_id=uuid.UUID(await dispute(client, auth, "NW-1002", "13.1", "640.00")),
                instructions="",
                ctx=ctx(app, user="ana"),
            )
    finally:
        c.graph_services, c.limits = original, limits
