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
