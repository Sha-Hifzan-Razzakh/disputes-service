"""Tool adapter behaviour under timeouts, 429-style and 500-style failures (T14.9), in memory."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.client.client import Client
from mcp.server.mcpserver import MCPServer

from app.infrastructure.tools.gateway import McpToolGateway, TokenBucket
from app.infrastructure.tools.mcp_client import McpConnection, ToolTransportError
from mcp_servers.common import ToolFailure


def server(behaviour: str) -> MCPServer:
    srv = MCPServer(name="payments")

    @srv.tool()
    async def get_refund(tenant_id: str, idempotency_key: str) -> dict[str, Any]:
        if behaviour == "slow":
            await asyncio.sleep(2)
        if behaviour == "429":
            raise ToolFailure("rate_limited", "slow down")
        if behaviour == "500":
            raise RuntimeError("database exploded")  # crash: the SDK hides the message
        if behaviour == "bad":
            return {"unexpected": True}
        return {"found": False}

    return srv


async def test_timeout_is_a_transport_error_and_session_is_reset() -> None:
    conn = McpConnection("payments", lambda: Client(server("slow")), timeout_s=0.2)
    await conn.start()
    with pytest.raises(ToolTransportError, match="timed out"):
        await conn.call("get_refund", {"tenant_id": "t", "idempotency_key": "k"})
    assert not conn.started  # next call reconnects


class _NoApprovals:
    async def get(self, *_: Any) -> Any:  # lookups never need approvals
        raise AssertionError


class _NoLog(McpToolGateway):
    async def _log(self, *args: Any, **kwargs: Any) -> None:  # no DB in unit tests
        return None


@pytest.mark.parametrize(
    ("behaviour", "ok", "retryable", "attempts"),
    [("429", False, True, 2), ("500", False, True, 2), ("bad", False, False, 1), ("fine", True, False, 1)],
)
async def test_gateway_classifies_provider_failures(behaviour: str, ok: bool, retryable: bool, attempts: int) -> None:
    conn = McpConnection("payments", lambda: Client(server(behaviour)), timeout_s=1.0)
    gw = _NoLog(
        connections={"payments": conn},
        approvals=_NoApprovals(),
        sessions=None,  # type: ignore[arg-type]
        limiter=TokenBucket(100, 100),
        max_attempts=2,
        backoff_s=0.0,
    )
    gw._verified.add("payments")  # schema pins are covered elsewhere; this server is a stand-in
    res = await gw.call("payments.get_refund", {"idempotency_key": "k"}, tenant_id="t")
    assert (res.ok, res.retryable, res.data["attempts"]) == (ok, retryable, attempts), res
    await conn.close()


class _Unreadable:
    """A provider that performs the refund but answers with something that is not JSON (e.g. a proxy page)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __aenter__(self) -> _Unreadable:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def list_tools(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(tools=[])  # pins are not under test here

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(text="<html>OK</html>")], is_error=False, structured_content=None
        )


class _BoundApproval:
    def __init__(self, dispute_id: Any, key: str) -> None:
        self.dispute_id, self.key = dispute_id, key

    async def get(self, *_: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            status="approved",
            dispute_id=self.dispute_id,
            summary={
                "tool": "payments.issue_refund",
                "idempotency_key": self.key,
                "amount": "10.00",
                "currency": "USD",
            },
        )


async def test_r3_unreadable_success_from_a_side_effect_is_unknown_not_failed() -> None:
    """Review R3: a refund that succeeded but answered non-JSON must be reconciled (retryable → unknown), never
    recorded as a clean failure that a new resolution could pay again."""
    import uuid

    fake = _Unreadable()
    conn = McpConnection("payments", lambda: fake, timeout_s=1.0)
    did = uuid.uuid4()
    gw = _NoLog(
        connections={"payments": conn},
        approvals=_BoundApproval(did, "k"),  # type: ignore[arg-type]
        sessions=None,  # type: ignore[arg-type]
        limiter=TokenBucket(100, 100),
        max_attempts=3,
        backoff_s=0.0,
    )
    gw._verified.add("payments")
    args = {"transaction_ref": "T", "amount": "10.00", "currency": "USD", "dispute_id": str(did)}
    res = await gw.call("payments.issue_refund", args, tenant_id="t", idempotency_key="k", approval_id=uuid.uuid4())
    assert not res.ok and res.retryable and res.data["attempts"] == 1 and fake.calls == 1
    assert "malformed_output" in (res.error or "")
    await conn.close()
