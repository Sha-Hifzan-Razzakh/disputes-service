"""Exercise the MCP servers over streamable HTTP with the production connection code (T12.12).

MCP_PAYMENTS_TOKEN=a MCP_NETWORK_TOKEN=b uvicorn mcp_servers.payments:app --port 8101 &
... uvicorn mcp_servers.network_gateway:app --port 8102 &
APP_MCP_PAYMENTS_TOKEN=a APP_MCP_NETWORK_TOKEN=b uv run python scripts/smoke_tools.py
"""

from __future__ import annotations

import asyncio

from app.bootstrap.settings import get_settings
from app.infrastructure.tools.mcp_client import McpConnection, ToolTransportError, http_session_factory
from app.infrastructure.tools.policy import pin_mismatches


async def main() -> None:
    s = get_settings()
    assert s.mcp_payments_token and s.mcp_network_token, "set APP_MCP_PAYMENTS_TOKEN and APP_MCP_NETWORK_TOKEN"
    pay = McpConnection(
        "payments",
        http_session_factory(s.mcp_payments_url, s.mcp_payments_token.get_secret_value(), s.tool_timeout_s),
        s.tool_timeout_s,
    )
    net = McpConnection(
        "network-gateway",
        http_session_factory(s.mcp_network_url, s.mcp_network_token.get_secret_value(), s.tool_timeout_s),
        s.tool_timeout_s,
    )
    for conn in (pay, net):
        await conn.start()
        print(f"{conn.name}: tools {sorted(conn.schemas)}")
    print("pin mismatches:", pin_mismatches({"payments": pay.schemas, "network-gateway": net.schemas}) or "none")
    print(
        "get_transaction:", await pay.call("get_transaction", {"tenant_id": "northwind", "transaction_ref": "NW-1003"})
    )
    refund = {
        "tenant_id": "northwind",
        "transaction_ref": "NW-1003",
        "amount": "10.00",
        "currency": "USD",
        "dispute_id": "smoke",
        "idempotency_key": "smoke-" + "0" * 58,
        "dry_run": True,
    }
    print("issue_refund dry_run:", await pay.call("issue_refund", refund))
    print("over-refund:", await pay.call("issue_refund", refund | {"amount": "500.00"}))
    print(
        "representment w/o evidence:",
        await net.call(
            "submit_representment",
            {
                "tenant_id": "northwind",
                "dispute_id": "smoke",
                "network": "VISA",
                "reason_code": "10.4",
                "evidence_ids": [],
                "rationale": "x",
                "idempotency_key": "smoke-net",
                "dry_run": True,
            },
        ),
    )
    bad = McpConnection("payments", http_session_factory(s.mcp_payments_url, "wrong-token", 2.0), 2.0)
    try:
        await bad.start()
        print("wrong token: ACCEPTED (unexpected)")
    except Exception as e:  # any transport failure counts as refused
        print("wrong token: refused,", type(e).__name__)
    try:
        await bad.call("get_refund", {"tenant_id": "northwind", "idempotency_key": "x"})
    except ToolTransportError as e:
        print("wrong token call:", e)
    for conn in (pay, net):
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
