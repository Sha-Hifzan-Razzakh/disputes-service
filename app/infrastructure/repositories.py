"""SQL implementations of the application repositories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import EvidenceItem, TimelineEntry, TransactionView
from app.domain.dispute import Dispute, DisputeStatus
from app.domain.errors import NotFound, VersionConflict
from app.domain.money import Money
from app.domain.reason_codes import Network
from app.infrastructure.db.models import DisputeEventRow, DisputeRow, EvidenceRow, TransactionRow


def _to_domain(r: DisputeRow) -> Dispute:
    return Dispute(
        id=r.id,
        tenant_id=r.tenant_id,
        network=Network(r.network),
        reason_code=r.reason_code,
        transaction_ref=r.transaction_ref,
        amount=Money(r.amount, r.currency),
        status=DisputeStatus(r.status),
        opened_at=r.opened_at,
        respond_by=r.respond_by,
        assigned_to=r.assigned_to,
        version=r.version,
    )


class SqlDisputeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, tenant_id: str, dispute_id: uuid.UUID) -> Dispute:
        row = await self.s.scalar(
            select(DisputeRow).where(DisputeRow.id == dispute_id, DisputeRow.tenant_id == tenant_id)
        )
        if row is None:
            raise NotFound("dispute", str(dispute_id))
        return _to_domain(row)

    async def page(self, tenant_id: str, *, status: str | None, limit: int, offset: int) -> Sequence[Dispute]:
        q = select(DisputeRow).where(DisputeRow.tenant_id == tenant_id)
        if status:
            q = q.where(DisputeRow.status == status)
        q = q.order_by(DisputeRow.respond_by.asc(), DisputeRow.id).limit(limit).offset(offset)
        return [_to_domain(r) for r in (await self.s.scalars(q)).all()]

    async def add(self, d: Dispute) -> None:
        self.s.add(
            DisputeRow(
                id=d.id,
                tenant_id=d.tenant_id,
                network=d.network.value,
                reason_code=d.reason_code,
                transaction_ref=d.transaction_ref,
                amount=d.amount.amount,
                currency=d.amount.currency,
                status=d.status.value,
                opened_at=d.opened_at,
                respond_by=d.respond_by,
                assigned_to=d.assigned_to,
                version=d.version,
            )
        )
        await self.s.flush()
        await self._flush_events(d)

    async def save(self, d: Dispute, *, expected_version: int) -> None:
        res = await self.s.execute(
            update(DisputeRow)
            .where(DisputeRow.id == d.id, DisputeRow.tenant_id == d.tenant_id, DisputeRow.version == expected_version)
            .values(status=d.status.value, assigned_to=d.assigned_to, version=d.version)
        )
        if res.rowcount != 1:  # type: ignore[attr-defined]
            current = await self.get(d.tenant_id, d.id)
            raise VersionConflict(expected_version, current.version)
        await self._flush_events(d)

    async def _flush_events(self, d: Dispute) -> None:
        for e in d.pending_events:
            self.s.add(
                DisputeEventRow(
                    dispute_id=d.id, tenant_id=d.tenant_id, at=e.at, actor=e.actor, kind=e.kind, payload=e.payload
                )
            )
        d.pending_events.clear()
        await self.s.flush()

    async def timeline(self, tenant_id: str, dispute_id: uuid.UUID) -> list[TimelineEntry]:
        rows = (
            await self.s.scalars(
                select(DisputeEventRow)
                .where(DisputeEventRow.dispute_id == dispute_id, DisputeEventRow.tenant_id == tenant_id)
                .order_by(DisputeEventRow.id)
            )
        ).all()
        return [TimelineEntry(at=r.at, actor=r.actor, kind=r.kind, payload=r.payload) for r in rows]


class SqlEvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(self, item: EvidenceItem) -> None:
        self.s.add(
            EvidenceRow(
                id=item.id,
                dispute_id=item.dispute_id,
                tenant_id=item.tenant_id,
                kind=item.kind,
                description=item.description,
                uri=item.uri,
                submitted_by=item.submitted_by,
                submitted_at=item.submitted_at,
            )
        )
        await self.s.flush()

    async def for_dispute(self, tenant_id: str, dispute_id: uuid.UUID) -> list[EvidenceItem]:
        rows = (
            await self.s.scalars(
                select(EvidenceRow)
                .where(EvidenceRow.dispute_id == dispute_id, EvidenceRow.tenant_id == tenant_id)
                .order_by(EvidenceRow.submitted_at)
            )
        ).all()
        return [
            EvidenceItem(
                id=r.id,
                dispute_id=r.dispute_id,
                tenant_id=r.tenant_id,
                kind=r.kind,
                description=r.description,
                uri=r.uri,
                submitted_by=r.submitted_by,
                submitted_at=r.submitted_at,
            )
            for r in rows
        ]


class SqlLedger:
    """Reads the ledger mirror. In production this is fed by the payments platform."""

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get_transaction(self, tenant_id: str, ref: str) -> TransactionView:
        r = await self.s.scalar(
            select(TransactionRow).where(TransactionRow.tenant_id == tenant_id, TransactionRow.ref == ref)
        )
        if r is None:
            raise NotFound("transaction", ref)
        return TransactionView(
            ref=r.ref,
            amount=Money(r.amount, r.currency),
            captured_at=r.captured_at,
            card_brand=r.card_brand,
            avs_result=r.avs_result,
            cvv_result=r.cvv_result,
            three_ds_authenticated=r.three_ds_authenticated,
            delivery_status=r.delivery_status,
            delivery_tracking=r.delivery_tracking,
            refunded=Money(r.refunded_amount, r.currency),
            description=r.description,
        )
