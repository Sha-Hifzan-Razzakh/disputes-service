"""Use cases for disputes, evidence and policy ingestion."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.ports import (
    DisputeRepository,
    EvidenceItem,
    EvidenceRepository,
    Ledger,
    PolicyStore,
    TimelineEntry,
)
from app.domain.dispute import Dispute
from app.domain.errors import DomainError, VersionConflict
from app.domain.money import Money
from app.domain.reason_codes import lookup

EVIDENCE_KINDS = {
    "three_ds_result",
    "avs_cvv_result",
    "delivery_proof",
    "product_description",
    "customer_comms",
    "transaction_records",
    "refund_record",
    "other",
}


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class DisputeService:
    disputes: DisputeRepository
    evidence: EvidenceRepository
    ledger: Ledger
    clock: SystemClock

    async def open(
        self, *, tenant_id: str, actor: str, network: str, reason_code: str, transaction_ref: str, amount: Money
    ) -> Dispute:
        reason = lookup(network, reason_code)
        txn = await self.ledger.get_transaction(tenant_id, transaction_ref)
        if txn.amount.currency != amount.currency:
            raise DomainError("dispute currency must match the transaction currency")
        d = Dispute.open(
            tenant_id=tenant_id,
            reason=reason,
            transaction_ref=transaction_ref,
            amount=amount,
            transaction_amount=txn.amount,
            actor=actor,
            now=self.clock.now(),
        )
        await self.disputes.add(d)
        return d

    async def get(self, tenant_id: str, dispute_id: uuid.UUID) -> Dispute:
        return await self.disputes.get(tenant_id, dispute_id)

    async def page(self, tenant_id: str, *, status: str | None, limit: int, offset: int) -> Sequence[Dispute]:
        return await self.disputes.page(tenant_id, status=status, limit=limit, offset=offset)

    async def act(
        self,
        *,
        tenant_id: str,
        dispute_id: uuid.UUID,
        actor: str,
        expected_version: int,
        action: str,
        assign_to: str | None = None,
        note: str | None = None,
    ) -> Dispute:
        d = await self.disputes.get(tenant_id, dispute_id)
        if d.version != expected_version:
            raise VersionConflict(expected_version, d.version)
        if action == "assign":
            if not assign_to:
                raise DomainError("assign requires assign_to")
            d.assign(assign_to, actor=actor, now=self.clock.now())
        else:
            d.apply(action, actor=actor, now=self.clock.now(), note=note)
        await self.disputes.save(d, expected_version=expected_version)
        return d

    async def add_evidence(
        self, *, tenant_id: str, dispute_id: uuid.UUID, actor: str, kind: str, description: str, uri: str | None
    ) -> EvidenceItem:
        if kind not in EVIDENCE_KINDS:
            raise DomainError(f"unknown evidence kind {kind}")
        d = await self.disputes.get(tenant_id, dispute_id)
        if d.is_terminal:
            raise DomainError("dispute is closed")
        item = EvidenceItem(
            id=uuid.uuid4(),
            dispute_id=d.id,
            tenant_id=tenant_id,
            kind=kind,
            description=description,
            uri=uri,
            submitted_by=actor,
            submitted_at=self.clock.now(),
        )
        await self.evidence.add(item)
        return item

    async def list_evidence(self, tenant_id: str, dispute_id: uuid.UUID) -> list[EvidenceItem]:
        await self.disputes.get(tenant_id, dispute_id)
        return await self.evidence.for_dispute(tenant_id, dispute_id)

    async def timeline(self, tenant_id: str, dispute_id: uuid.UUID) -> list[TimelineEntry]:
        await self.disputes.get(tenant_id, dispute_id)
        return await self.disputes.timeline(tenant_id, dispute_id)


@dataclass
class PolicyService:
    store: PolicyStore

    async def ingest(self, *, tenant_id: str | None, title: str, source: str, text: str) -> int:
        if len(text.strip()) < 40:
            raise DomainError("document text too short")
        return await self.store.add_document(tenant_id=tenant_id, title=title, source=source, text=text)
