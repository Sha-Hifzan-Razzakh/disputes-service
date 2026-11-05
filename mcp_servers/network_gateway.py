"""Card-network gateway MCP server (simulated): submit representments idempotently, look them up by key.

MCP_NETWORK_TOKEN=... uvicorn mcp_servers.network_gateway:app --port 8102
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from mcp_servers.common import FailureInjection, ToolFailure, transport_security, with_bearer_auth

NETWORKS = {"VISA", "MASTERCARD"}


@dataclass
class NetworkGatewayStore:
    submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    faults: FailureInjection = field(default_factory=FailureInjection)


def create_server(store: NetworkGatewayStore) -> MCPServer:
    srv = MCPServer(name="network-gateway", version="1.0.0", instructions="Representment submissions to card networks.")

    @srv.tool(
        description="Submit a representment (contest) for a dispute with the cited evidence. Idempotent on "
        "idempotency_key. dry_run validates the package without submitting."
    )
    async def submit_representment(
        tenant_id: str,
        dispute_id: str,
        network: str,
        reason_code: str,
        evidence_ids: list[str],
        rationale: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        store.faults.check("submit_representment")
        if network not in NETWORKS:
            raise ToolFailure("unsupported_network", network)
        if not evidence_ids:
            raise ToolFailure("missing_evidence", "a representment needs at least one evidence item")
        if len(rationale) > 4000:
            raise ToolFailure("rationale_too_long", "max 4000 characters")
        async with store.lock:
            done = store.submissions.get(idempotency_key)
            request = {
                "dispute_id": dispute_id,
                "network": network,
                "reason_code": reason_code,
                "evidence_ids": list(evidence_ids),
            }
            if done is not None:
                if done["tenant_id"] != tenant_id:
                    raise ToolFailure("forbidden", "idempotency key belongs to another tenant")
                if done.get("request", request) != request:
                    raise ToolFailure("idempotency_key_reused", "this key was used for a different submission")
                return {**done["result"], "replayed": True}
            if dry_run:
                return {"dry_run": True, "accepted": True}
            result = {
                "external_ref": f"rp_{len(store.submissions) + 1:06d}",
                "status": "submitted",
                "dispute_id": dispute_id,
                "network": network,
                "evidence_count": len(evidence_ids),
            }
            store.submissions[idempotency_key] = {"tenant_id": tenant_id, "result": result, "request": request}
            return {**result, "replayed": False}

    @srv.tool(description="Look a representment up by idempotency key (reconciliation).")
    async def get_submission(tenant_id: str, idempotency_key: str) -> dict[str, Any]:
        done = store.submissions.get(idempotency_key)
        if done is None or done["tenant_id"] != tenant_id:
            return {"found": False}
        return {"found": True, **done["result"]}

    return srv


STORE = NetworkGatewayStore()
server = create_server(STORE)


def http_app() -> Starlette:
    return with_bearer_auth(
        server.streamable_http_app(
            stateless_http=True, json_response=True, transport_security=transport_security("network-gateway")
        ),
        "MCP_NETWORK_TOKEN",
    )


app = http_app()
