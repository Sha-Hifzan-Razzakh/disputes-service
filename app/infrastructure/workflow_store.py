"""SQL adapters for the resolution workflow ports. Each opens its own short transaction from the sessionmaker, so
graph nodes never hold a DB session across a super-step. Connection-level failures surface as TransientError."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.application.case import CaseService
from app.application.errors import ApprovalDecided, ApprovalExpired, ResolutionInProgress
from app.application.ports import (
    ApprovalRecord,
    CaseSnapshot,
    SideEffectRecord,
    TenantDisputePolicy,
    ThreadRecord,
)
from app.domain.dispute import DisputeStatus, path_to
from app.domain.errors import NotFound, VersionConflict
from app.domain.money import Money
from app.infrastructure.db.models import (
    ApprovalRow,
    AuditLogRow,
    OutboxRow,
    SideEffectRow,
    TenantDisputePolicyRow,
    TenantRow,
    ThreadRow,
)
from app.infrastructure.db.session import Sessions, transaction
from app.infrastructure.repositories import SqlDisputeRepository, SqlEvidenceRepository, SqlLedger


class SqlCaseReader:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def snapshot(self, tenant_id: str, dispute_id: uuid.UUID) -> CaseSnapshot:
        async with transaction(self.sessions) as s:
            return await CaseService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s)).snapshot(
                tenant_id, dispute_id
            )


class SqlTenantPolicyRepository:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def get(self, tenant_id: str) -> TenantDisputePolicy:
        async with transaction(self.sessions) as s:
            row = (
                await s.execute(
                    select(TenantDisputePolicyRow, TenantRow.currency)
                    .join(TenantRow, TenantRow.id == TenantDisputePolicyRow.tenant_id)
                    .where(TenantDisputePolicyRow.tenant_id == tenant_id)
                )
            ).first()
        if row is None:
            raise NotFound("tenant dispute policy", tenant_id)
        p, cur = row
        return TenantDisputePolicy(
            tenant_id=tenant_id,
            currency=cur,
            approval_threshold=Money(p.approval_threshold, cur),
            auto_approve_below=Money(p.auto_approve_below, cur) if p.auto_approve_below is not None else None,
            partial_pct=p.partial_pct,
        )


def _thread(r: ThreadRow) -> ThreadRecord:
    return ThreadRecord(
        r.thread_id, r.tenant_id, r.dispute_id, r.graph, r.status, r.started_by, r.created_at, r.outcome
    )


class SqlThreadRegistry:
    ACTIVE = ("running", "awaiting_approval", "resume_failed")

    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def start(self, *, tenant_id: str, dispute_id: uuid.UUID, graph: str, started_by: str) -> str:
        try:
            async with transaction(self.sessions) as s:
                n = await s.scalar(
                    select(func.count())
                    .select_from(ThreadRow)
                    .where(ThreadRow.dispute_id == dispute_id, ThreadRow.graph == graph)
                )
                thread_id = f"{graph}:{dispute_id}:{(n or 0) + 1}"
                s.add(
                    ThreadRow(
                        thread_id=thread_id,
                        tenant_id=tenant_id,
                        dispute_id=dispute_id,
                        graph=graph,
                        status="running",
                        started_by=started_by,
                    )
                )
            return thread_id
        except IntegrityError:
            raise ResolutionInProgress(str(dispute_id), await self.active_for(tenant_id, dispute_id)) from None

    async def get(self, tenant_id: str, thread_id: str) -> ThreadRecord:
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == thread_id, ThreadRow.tenant_id == tenant_id)
            )
        if row is None:
            raise NotFound("thread", thread_id)
        return _thread(row)

    async def set_status(self, thread_id: str, status: str, *, outcome: str | None = None) -> None:
        values: dict[str, Any] = {"status": status}
        if outcome is not None:
            values["outcome"] = outcome
        async with transaction(self.sessions) as s:
            await s.execute(update(ThreadRow).where(ThreadRow.thread_id == thread_id).values(**values))

    async def active_for(self, tenant_id: str, dispute_id: uuid.UUID) -> str | None:
        async with transaction(self.sessions) as s:
            return await s.scalar(
                select(ThreadRow.thread_id).where(
                    ThreadRow.tenant_id == tenant_id,
                    ThreadRow.dispute_id == dispute_id,
                    ThreadRow.status.in_(self.ACTIVE),
                )
            )


class SqlDisputeCommands:
    """Moves a dispute toward a goal status along allowed transitions; replays are no-ops (idempotent)."""

    def __init__(self, sessions: Sessions, clock: Any) -> None:
        self.sessions = sessions
        self.clock = clock

    async def advance(self, *, tenant_id: str, dispute_id: uuid.UUID, goal: str, actor: str, note: str) -> str:
        for attempt in (1, 2):  # one re-read on a concurrent edit
            try:
                async with transaction(self.sessions) as s:
                    repo = SqlDisputeRepository(s)
                    d = await repo.get(tenant_id, dispute_id)
                    expected = d.version
                    for action in path_to(d.status, DisputeStatus(goal)):
                        d.apply(action, actor=actor, now=self.clock.now(), note=note)
                    if d.version != expected:
                        await repo.save(d, expected_version=expected)
                    return d.status.value
            except VersionConflict:
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")


def _approval(r: ApprovalRow) -> ApprovalRecord:
    return ApprovalRecord(
        id=r.id,
        tenant_id=r.tenant_id,
        dispute_id=r.dispute_id,
        thread_id=r.thread_id,
        requested_by=r.requested_by,
        required_role=r.required_role,
        priority=r.priority,
        status=r.status,
        summary=r.summary,
        created_at=r.created_at,
        expires_at=r.expires_at,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        note=r.note,
    )


class SqlApprovalRepository:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        async with transaction(self.sessions) as s:
            await s.execute(
                insert(ApprovalRow)
                .values(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    dispute_id=record.dispute_id,
                    thread_id=record.thread_id,
                    requested_by=record.requested_by,
                    required_role=record.required_role,
                    priority=record.priority,
                    status=record.status,
                    summary=record.summary,
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                    decided_by=record.decided_by,
                    decided_at=record.decided_at,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            row = await s.get(ApprovalRow, record.id)
        assert row is not None
        return _approval(row)

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord:
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                select(ApprovalRow).where(ApprovalRow.id == approval_id, ApprovalRow.tenant_id == tenant_id)
            )
        if row is None:
            raise NotFound("approval", str(approval_id))
        return _approval(row)

    async def decide(
        self, *, tenant_id: str, approval_id: uuid.UUID, status: str, actor: str, note: str | None, now: datetime
    ) -> ApprovalRecord:
        """Compare-and-set from pending. The losing caller sees ApprovalDecided / ApprovalExpired."""
        async with transaction(self.sessions) as s:
            row = await s.scalar(
                update(ApprovalRow)
                .where(
                    ApprovalRow.id == approval_id,
                    ApprovalRow.tenant_id == tenant_id,
                    ApprovalRow.status == "pending",
                    (ApprovalRow.expires_at > now) if status != "expired" else (ApprovalRow.expires_at <= now),
                )
                .values(status=status, decided_by=actor, decided_at=now, note=note)
                .returning(ApprovalRow)
            )
            if row is not None:
                return _approval(row)
            current = await s.scalar(
                select(ApprovalRow).where(ApprovalRow.id == approval_id, ApprovalRow.tenant_id == tenant_id)
            )
        if current is None:
            raise NotFound("approval", str(approval_id))
        if current.status == "pending" or current.status == "expired":
            raise ApprovalExpired(f"approval {approval_id} expired at {current.expires_at.isoformat()}")
        raise ApprovalDecided(f"approval {approval_id} already {current.status}")

    async def pending(
        self, tenant_id: str, *, roles: Sequence[str], exclude_requester: str, limit: int
    ) -> list[ApprovalRecord]:
        async with transaction(self.sessions) as s:
            rows = (
                await s.scalars(
                    select(ApprovalRow)
                    .where(
                        ApprovalRow.tenant_id == tenant_id,
                        ApprovalRow.status == "pending",
                        ApprovalRow.required_role.in_(list(roles)),
                        ApprovalRow.requested_by != exclude_requester,
                    )
                    .order_by(ApprovalRow.priority.desc(), ApprovalRow.expires_at, ApprovalRow.id)
                    .limit(limit)
                )
            ).all()
        return [_approval(r) for r in rows]

    async def due_for_expiry(self, now: datetime, *, limit: int) -> list[ApprovalRecord]:
        async with transaction(self.sessions) as s:
            rows = (
                await s.scalars(
                    select(ApprovalRow)
                    .where(ApprovalRow.status == "pending", ApprovalRow.expires_at <= now)
                    .order_by(ApprovalRow.expires_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
        return [_approval(r) for r in rows]


class SqlSideEffectLedger:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def reserve(self, record: SideEffectRecord) -> SideEffectRecord:
        async with transaction(self.sessions) as s:
            await s.execute(
                insert(SideEffectRow)
                .values(
                    idempotency_key=record.idempotency_key,
                    tenant_id=record.tenant_id,
                    dispute_id=record.dispute_id,
                    kind=record.kind,
                    status="pending",
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            row = await s.get(SideEffectRow, record.idempotency_key)
        assert row is not None
        return SideEffectRecord(
            row.idempotency_key, row.tenant_id, row.dispute_id, row.kind, row.status, row.external_ref, row.error
        )

    async def finish(self, key: str, *, status: str, external_ref: str | None, error: str | None) -> None:
        async with transaction(self.sessions) as s:
            await s.execute(
                update(SideEffectRow)
                .where(SideEffectRow.idempotency_key == key, SideEffectRow.status != "succeeded")
                .values(status=status, external_ref=external_ref, error=error)
            )

    async def stale_unknown(self, older_than: datetime, *, limit: int) -> list[SideEffectRecord]:
        async with transaction(self.sessions) as s:
            rows = (
                await s.scalars(
                    select(SideEffectRow)
                    .where(SideEffectRow.status.in_(("unknown", "pending")), SideEffectRow.updated_at < older_than)
                    .order_by(SideEffectRow.updated_at)
                    .limit(limit)
                )
            ).all()
        return [
            SideEffectRecord(r.idempotency_key, r.tenant_id, r.dispute_id, r.kind, r.status, r.external_ref, r.error)
            for r in rows
        ]


class SqlAuditLog:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def record(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        subject: str,
        request_id: str,
        data: dict[str, object],
        dedupe_key: str | None = None,
    ) -> None:
        async with transaction(self.sessions) as s:
            await s.execute(
                insert(AuditLogRow)
                .values(
                    tenant_id=tenant_id,
                    actor=actor,
                    action=action,
                    subject=subject,
                    request_id=request_id,
                    data=data,
                    dedupe_key=dedupe_key,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )


class SqlOutbox:
    def __init__(self, sessions: Sessions) -> None:
        self.sessions = sessions

    async def enqueue(self, *, tenant_id: str, type: str, payload: dict[str, object], dedupe_key: str) -> None:
        async with transaction(self.sessions) as s:
            await s.execute(
                insert(OutboxRow)
                .values(tenant_id=tenant_id, type=type, payload=payload, dedupe_key=dedupe_key)
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
