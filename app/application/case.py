"""Reads the case a graph run works on: dispute, ledger transaction and merchant evidence, all tenant-filtered."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.application.ports import CaseSnapshot, DisputeRepository, EvidenceRepository, Ledger


@dataclass
class CaseService:
    disputes: DisputeRepository
    evidence: EvidenceRepository
    ledger: Ledger

    async def snapshot(self, tenant_id: str, dispute_id: uuid.UUID) -> CaseSnapshot:
        d = await self.disputes.get(tenant_id, dispute_id)
        txn = await self.ledger.get_transaction(tenant_id, d.transaction_ref)
        items = await self.evidence.for_dispute(tenant_id, dispute_id)
        return CaseSnapshot(dispute=d, transaction=txn, evidence=tuple(items))


def case_to_state(c: CaseSnapshot) -> dict[str, Any]:
    """JSON-safe view of the snapshot for graph state (ADR-0002): strings for money and times."""
    d, t = c.dispute, c.transaction
    return {
        "dispute": {
            "id": str(d.id),
            "network": d.network.value,
            "reason_code": d.reason_code,
            "amount": str(d.amount.amount),
            "currency": d.amount.currency,
            "status": d.status.value,
            "respond_by": d.respond_by.isoformat(),
            "version": d.version,
        },
        "transaction": {
            "ref": t.ref,
            "amount": str(t.amount.amount),
            "three_ds_authenticated": t.three_ds_authenticated,
            "avs_result": t.avs_result,
            "cvv_result": t.cvv_result,
            "delivery_status": t.delivery_status,
            "delivery_tracking": t.delivery_tracking,
            "refunded": str(t.refunded.amount),
        },
        "evidence": [{"id": str(e.id), "kind": e.kind, "description": e.description[:400]} for e in c.evidence],
    }
