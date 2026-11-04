"""Tool gateway pipeline against real MCP servers (in memory) and the real approvals/tool_calls tables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.application.ports import ApprovalRecord
from app.infrastructure.tools.gateway import TokenBucket
from tests.graph.test_resolution_graph import dispute, sql


async def _approval(app: Any, did: str, status: str = "approved", tenant: str = "northwind") -> uuid.UUID:
    aid = uuid.uuid4()
    tid = f"resolution:{did}:{uuid.uuid4().hex[:6]}"
    sql(
        "INSERT INTO threads (thread_id, tenant_id, dispute_id, graph, status, started_by) "
        "VALUES (%s, %s, %s, 'resolution', 'running', 'ana')",
        tid,
        tenant,
        did,
    )
    now = datetime.now(UTC)
    await app.state.container.graph_services.approvals.create(
        ApprovalRecord(
            id=aid,
            tenant_id=tenant,
            dispute_id=uuid.UUID(did),
            thread_id=tid,
            requested_by="ana",
            required_role="analyst",
            priority="normal",
            status=status,
            summary={},
            created_at=now,
            expires_at=now + timedelta(hours=1),
            decided_by="ben" if status != "pending" else None,
        )
    )
    return aid


REFUND = {"transaction_ref": "NW-1003", "amount": "10.00", "currency": "USD", "dispute_id": "x"}


async def test_deny_by_default_and_guards_are_logged(client, auth, app, mcp_tools) -> None:
    gw = mcp_tools["gateway"]
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    pending = await _approval(app, did, "pending")
    cases = [
        (("payments.delete_everything", {}), {}, "not allowed"),
        (("payments.issue_refund", REFUND), {"approval_id": None, "idempotency_key": "k"}, "needs an approval"),
        (("payments.issue_refund", REFUND), {"approval_id": pending, "idempotency_key": "k"}, "approval is pending"),
        (("payments.issue_refund", REFUND), {"approval_id": pending}, "idempotency key"),
        (("payments.issue_refund", REFUND | {"tenant_id": "aurora"}), {"idempotency_key": "k"}, "tenant mismatch"),
    ]
    for (tool, args), kw, why in cases:
        res = await gw.call(tool, dict(args), tenant_id="northwind", **kw)
        assert not res.ok and not res.retryable and why in (res.error or ""), (tool, res)
    other_tenant = await gw.call(
        "payments.issue_refund", dict(REFUND), tenant_id="aurora", idempotency_key="k", approval_id=pending
    )
    assert "approval not found" in (other_tenant.error or "")
    assert mcp_tools["ledger"].refunds == {}
    rows = sql("SELECT status, count(*) FROM tool_calls WHERE status = 'denied' GROUP BY 1")
    assert rows and rows[0][1] >= 6


async def test_transient_failures_retry_with_the_same_key_then_succeed(client, auth, app, mcp_tools) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    aid = await _approval(app, did)
    mcp_tools["ledger"].faults.fail_next("issue_refund", "temporarily_unavailable", times=2)
    key = uuid.uuid4().hex
    res = await mcp_tools["gateway"].call(
        "payments.issue_refund", dict(REFUND), tenant_id="northwind", idempotency_key=key, approval_id=aid
    )
    assert res.ok and res.data["attempts"] == 3 and res.data["external_ref"].startswith("rf_")
    assert sql("SELECT attempt, status FROM tool_calls WHERE idempotency_key = %s ORDER BY attempt", key) == [
        (1, "retryable_error"),
        (2, "retryable_error"),
        (3, "ok"),
    ]
    args_hash = sql("SELECT DISTINCT args_sha256 FROM tool_calls WHERE idempotency_key = %s", key)
    assert len(args_hash) == 1 and len(args_hash[0][0]) == 64  # same args every attempt, never stored in clear


async def test_exhausted_retries_are_retryable_and_permanent_errors_are_not(client, auth, app, mcp_tools) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    aid = await _approval(app, did)
    mcp_tools["ledger"].faults.fail_next("issue_refund", "temporarily_unavailable", times=3)
    res = await mcp_tools["gateway"].call(
        "payments.issue_refund", dict(REFUND), tenant_id="northwind", idempotency_key=uuid.uuid4().hex, approval_id=aid
    )
    assert not res.ok and res.retryable and res.data["attempts"] == 3
    res = await mcp_tools["gateway"].call(
        "payments.issue_refund",
        dict(REFUND) | {"amount": "999.00"},
        tenant_id="northwind",
        idempotency_key=uuid.uuid4().hex,
        approval_id=aid,
    )
    assert not res.ok and not res.retryable and res.error.startswith("refund_exceeds_capture")


async def test_rate_limit_per_tenant_per_tool(client, auth, app, mcp_tools) -> None:
    gw = mcp_tools["gateway"]
    gw.limiter = TokenBucket(rate=0.001, burst=2)
    results = [
        await gw.call("payments.get_transaction", {"transaction_ref": "NW-1001"}, tenant_id="northwind")
        for _ in range(3)
    ]
    assert [r.ok for r in results] == [True, True, False] and results[2].error == "rate_limited"
    assert (await gw.call("payments.get_transaction", {"transaction_ref": "AU-2001"}, tenant_id="aurora")).ok


async def test_pin_mismatch_disables_only_that_tool(app, mcp_tools) -> None:
    gw = mcp_tools["gateway"]
    gw.disabled = {"payments.get_transaction": "input schema differs from the reviewed pin"}
    res = await gw.call("payments.get_transaction", {"transaction_ref": "NW-1001"}, tenant_id="northwind")
    assert not res.ok and "disabled" in res.error
    assert (await gw.call("payments.get_refund", {"idempotency_key": "nope"}, tenant_id="northwind")).ok


async def test_unreachable_server_degrades_to_retryable_and_recovers_on_next_call(app, mcp_tools) -> None:
    from contextlib import asynccontextmanager

    from app.infrastructure.tools.gateway import McpToolGateway
    from app.infrastructure.tools.mcp_client import McpConnection

    real = mcp_tools["connections"]["payments"]
    down = {"value": True}

    @asynccontextmanager
    async def flaky():  # type: ignore[no-untyped-def]
        if down["value"]:
            raise ConnectionRefusedError("payments is down")
        async with real._factory() as client:
            yield client

    conn = McpConnection("payments", flaky, 2.0)
    gw = McpToolGateway(
        connections={"payments": conn},
        approvals=app.state.container.graph_services.approvals,
        sessions=app.state.container.sessions,
        limiter=TokenBucket(100, 100),
        max_attempts=2,
        backoff_s=0.01,
    )
    first = await gw.call("payments.get_refund", {"idempotency_key": "nope"}, tenant_id="northwind")
    assert not first.ok and first.retryable and "cannot connect" in first.error
    down["value"] = False
    second = await gw.call("payments.get_refund", {"idempotency_key": "nope"}, tenant_id="northwind")
    assert second.ok and second.data["found"] is False and "payments.get_refund" not in gw.disabled
    await conn.close()
