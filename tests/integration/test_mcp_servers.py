"""MCP servers through the real protocol (in-memory transport): contracts, idempotency, tenant binding, pins."""

from __future__ import annotations

import json

from mcp.client.client import Client

from app.infrastructure.tools.policy import pin_mismatches
from mcp_servers import network_gateway, payments


def _json(res) -> dict:  # type: ignore[no-untyped-def]
    return res.structured_content or json.loads(res.content[0].text)


async def test_refund_is_idempotent_and_bounded_by_the_capture() -> None:
    ledger = payments.PaymentsLedger.from_seeds()
    async with Client(payments.create_server(ledger)) as c:
        args = {
            "tenant_id": "northwind",
            "transaction_ref": "NW-1003",
            "amount": "50.00",
            "currency": "USD",
            "dispute_id": "d1",
            "idempotency_key": "k1",
        }
        first = _json(await c.call_tool("issue_refund", args))
        again = _json(await c.call_tool("issue_refund", args))
        assert first["external_ref"] == again["external_ref"] and again["replayed"] is True
        assert ledger.transactions[("northwind", "NW-1003")]["refunded"] == 50
        over = await c.call_tool("issue_refund", args | {"idempotency_key": "k2", "amount": "40.00"})
        assert over.is_error and "refund_exceeds_capture" in over.content[0].text
        dry = _json(
            await c.call_tool("issue_refund", args | {"idempotency_key": "k3", "amount": "39.50", "dry_run": True})
        )
        assert dry["dry_run"] and ledger.transactions[("northwind", "NW-1003")]["refunded"] == 50


async def test_keys_and_lookups_are_tenant_bound() -> None:
    ledger = payments.PaymentsLedger.from_seeds()
    async with Client(payments.create_server(ledger)) as c:
        await c.call_tool(
            "issue_refund",
            {
                "tenant_id": "northwind",
                "transaction_ref": "NW-1001",
                "amount": "1.00",
                "currency": "USD",
                "dispute_id": "d",
                "idempotency_key": "kk",
            },
        )
        steal = await c.call_tool(
            "issue_refund",
            {
                "tenant_id": "aurora",
                "transaction_ref": "AU-2001",
                "amount": "1.00",
                "currency": "EUR",
                "dispute_id": "d",
                "idempotency_key": "kk",
            },
        )
        assert steal.is_error and "forbidden" in steal.content[0].text
        assert _json(await c.call_tool("get_refund", {"tenant_id": "aurora", "idempotency_key": "kk"})) == {
            "found": False
        }
        assert _json(await c.call_tool("get_refund", {"tenant_id": "northwind", "idempotency_key": "kk"}))["found"]
        missing = await c.call_tool("get_transaction", {"tenant_id": "aurora", "transaction_ref": "NW-1001"})
        assert missing.is_error and "not_found:" in missing.content[0].text


async def test_representment_needs_evidence_and_dedupes() -> None:
    store = network_gateway.NetworkGatewayStore()
    async with Client(network_gateway.create_server(store)) as c:
        base = {
            "tenant_id": "northwind",
            "dispute_id": "d",
            "network": "VISA",
            "reason_code": "10.4",
            "rationale": "3DS",
            "idempotency_key": "r1",
        }
        empty = await c.call_tool("submit_representment", base | {"evidence_ids": []})
        assert empty.is_error and "missing_evidence" in empty.content[0].text
        a = _json(await c.call_tool("submit_representment", base | {"evidence_ids": ["ledger:three_ds"]}))
        b = _json(await c.call_tool("submit_representment", base | {"evidence_ids": ["ledger:three_ds"]}))
        assert a["external_ref"] == b["external_ref"] and len(store.submissions) == 1


async def test_reviewed_pins_match_the_live_schemas() -> None:
    live = {}
    for name, srv in {"payments": payments.server, "network-gateway": network_gateway.server}.items():
        async with Client(srv) as c:
            live[name] = {t.name: dict(t.input_schema) for t in (await c.list_tools()).tools}
    assert pin_mismatches(live) == {}
    live["payments"]["issue_refund"]["properties"]["reason"] = {"type": "string"}  # an unreviewed change
    assert set(pin_mismatches(live)) == {"payments.issue_refund"}
