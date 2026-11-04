"""Runs the v1 assist graph for a dispute."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.application.ports import DisputeRepository, EvidenceRepository, Ledger


@dataclass
class AssistService:
    disputes: DisputeRepository
    evidence: EvidenceRepository
    ledger: Ledger
    graph: Any  # compiled LangGraph

    async def ask(self, *, tenant_id: str, dispute_id: uuid.UUID, question: str) -> dict[str, Any]:
        d = await self.disputes.get(tenant_id, dispute_id)
        txn = await self.ledger.get_transaction(tenant_id, d.transaction_ref)
        ev = await self.evidence.for_dispute(tenant_id, dispute_id)
        state = {
            "tenant_id": tenant_id,
            "dispute": {
                "id": str(d.id),
                "network": d.network.value,
                "reason_code": d.reason_code,
                "amount": str(d.amount.amount),
                "currency": d.amount.currency,
                "status": d.status.value,
                "respond_by": d.respond_by.isoformat(),
            },
            "transaction": {
                "ref": txn.ref,
                "amount": str(txn.amount.amount),
                "three_ds_authenticated": txn.three_ds_authenticated,
                "avs_result": txn.avs_result,
                "cvv_result": txn.cvv_result,
                "delivery_status": txn.delivery_status,
                "delivery_tracking": txn.delivery_tracking,
                "refunded": str(txn.refunded.amount),
            },
            "evidence_kinds": sorted({e.kind for e in ev}),
            "question": question,
        }
        config = {"configurable": {"thread_id": f"assist:{dispute_id}"}}
        out = await self.graph.ainvoke(state, config)
        return {
            "recommendation": out["recommendation"],
            "suggested_resolution": out["suggested_resolution"],
            "citations": out["citations"],
            "turns": len(out.get("history", [])),
        }
