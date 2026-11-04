"""Reads the case a graph run works on: dispute, ledger transaction and merchant evidence, all tenant-filtered."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

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
