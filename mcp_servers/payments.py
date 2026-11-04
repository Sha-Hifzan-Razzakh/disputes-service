"""Payments MCP server (simulated): read transactions, issue refunds idempotently, look refunds up by key.

MCP_PAYMENTS_TOKEN=... uvicorn mcp_servers.payments:app --port 8101
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from mcp.server.mcpserver import MCPServer

from mcp_servers.common import FailureInjection, ToolFailure, with_bearer_auth


@dataclass
class PaymentsLedger:
    transactions: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)  # (tenant, ref) -> txn
    refunds: dict[str, dict[str, Any]] = field(default_factory=dict)  # idempotency key -> refund
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    faults: FailureInjection = field(default_factory=FailureInjection)

    @classmethod
    def from_seeds(cls) -> PaymentsLedger:
        from seeds import data

        cur = {t["id"]: t["currency"] for t in data.TENANTS}
        led = cls()
        for tid, ref, amt, card, _avs, _cvv, _tds, _ds, _trk, refunded, _desc, _days in data.TRANSACTIONS:
            led.transactions[(tid, ref)] = {
                "ref": ref,
                "amount": Decimal(amt),
                "currency": cur[tid],
                "card_brand": card,
                "refunded": Decimal(refunded),
            }
        return led


def _amount(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ToolFailure("invalid_amount", f"{raw!r} is not a decimal") from None
    if value <= 0 or value != value.quantize(Decimal("0.01")):
        raise ToolFailure("invalid_amount", "amount must be positive with at most 2 decimals")
    return value


def create_server(ledger: PaymentsLedger) -> MCPServer:
    srv = MCPServer(name="payments", version="1.0.0", instructions="Refunds against captured card transactions.")

    @srv.tool(description="Read a captured transaction of the tenant (amount, currency, prior refunds).")
    async def get_transaction(tenant_id: str, transaction_ref: str) -> dict[str, Any]:
        ledger.faults.check("get_transaction")
        txn = ledger.transactions.get((tenant_id, transaction_ref))
        if txn is None:
            raise ToolFailure("not_found", f"transaction {transaction_ref} not found")
        return {
            "ref": txn["ref"],
            "amount": str(txn["amount"]),
            "currency": txn["currency"],
            "refunded": str(txn["refunded"]),
        }

    @srv.tool(
        description="Refund part or all of a captured transaction. Idempotent on idempotency_key: a repeated "
        "key returns the original refund. dry_run validates without moving money."
    )
    async def issue_refund(
        tenant_id: str,
        transaction_ref: str,
        amount: str,
        currency: str,
        dispute_id: str,
        idempotency_key: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        ledger.faults.check("issue_refund")
        async with ledger.lock:
            done = ledger.refunds.get(idempotency_key)
            if done is not None:
                if done["tenant_id"] != tenant_id:
                    raise ToolFailure("forbidden", "idempotency key belongs to another tenant")
                return {**done["result"], "replayed": True}
            txn = ledger.transactions.get((tenant_id, transaction_ref))
            if txn is None:
                raise ToolFailure("not_found", f"transaction {transaction_ref} not found")
            value = _amount(amount)
            if currency != txn["currency"]:
                raise ToolFailure("currency_mismatch", f"{currency} != {txn['currency']}")
            if txn["refunded"] + value > txn["amount"]:
                raise ToolFailure("refund_exceeds_capture", f"{txn['refunded']} + {value} > {txn['amount']}")
            if dry_run:
                return {"dry_run": True, "would_refund": str(value), "currency": currency}
            txn["refunded"] += value
            result = {
                "external_ref": f"rf_{len(ledger.refunds) + 1:06d}",
                "status": "succeeded",
                "amount": str(value),
                "currency": currency,
                "refunded_total": str(txn["refunded"]),
                "dispute_id": dispute_id,
            }
            ledger.refunds[idempotency_key] = {"tenant_id": tenant_id, "result": result}
            return {**result, "replayed": False}

    @srv.tool(description="Look a refund up by idempotency key (reconciliation).")
    async def get_refund(tenant_id: str, idempotency_key: str) -> dict[str, Any]:
        done = ledger.refunds.get(idempotency_key)
        if done is None or done["tenant_id"] != tenant_id:
            return {"found": False}
        return {"found": True, **done["result"]}

    return srv


LEDGER = PaymentsLedger.from_seeds()
server = create_server(LEDGER)
app = with_bearer_auth(server.streamable_http_app(stateless_http=True, json_response=True), "MCP_PAYMENTS_TOKEN")
