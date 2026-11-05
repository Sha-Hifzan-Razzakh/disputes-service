"""Reusable in-memory fakes for every port the graphs use (T14.2), plus helpers to build Ctx/Runtime.

They follow the adapters' contracts (idempotent inserts, CAS decisions, tenant filtering, advisory locking) so unit
and graph tests can run without Postgres. Behaviour that only Postgres can prove (partial unique index, real
locks across processes) stays in the integration suite."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.runtime import ExecutionInfo, Runtime

from app.application.errors import ApprovalDecided, ApprovalExpired, ResolutionInProgress, ThreadBusy
from app.application.ports import (
    ApprovalRecord,
    CaseSnapshot,
    EvidenceItem,
    PolicyHit,
    SideEffectRecord,
    TenantDisputePolicy,
    ThreadRecord,
    ToolResult,
    TransactionView,
    TransientError,
)
from app.domain.dispute import Dispute, DisputeStatus, path_to
from app.domain.errors import NotFound
from app.domain.money import Money
from app.domain.reason_codes import lookup
from app.infrastructure.drafting import RuleBasedDrafter, RuleBasedResolutionDrafter
from app.orchestration.context import Ctx, GraphLimits, GraphServices

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    at: datetime = NOW

    def now(self) -> datetime:
        return self.at

    def advance(self, **kw: float) -> None:
        self.at += timedelta(**kw)


@dataclass
class FakePolicyStore:
    """Returns the configured hits for every query (scores as given); records queries."""

    hits: list[PolicyHit] = field(default_factory=list)
    queries: list[tuple[str, str]] = field(default_factory=list)
    fail_times: int = 0

    async def add_document(self, *, tenant_id: str | None, title: str, source: str, text: str) -> int:
        return 0

    async def search(self, *, tenant_id: str, query: str, k: int) -> list[PolicyHit]:
        self.queries.append((tenant_id, query))
        if self.fail_times:
            self.fail_times -= 1
            raise TransientError("vector store down")
        return self.hits[:k]


def network_hit(cid: str = "n1", score: float = 0.5) -> PolicyHit:
    return PolicyHit(
        chunk_id=cid, document_title="Network rules", text="network rule text", score=score, scope="network"
    )


def tenant_hit(cid: str = "t1", score: float = 0.5) -> PolicyHit:
    return PolicyHit(
        chunk_id=cid, document_title="Merchant policy", text="merchant policy", score=score, scope="tenant"
    )


@dataclass
class MemCases:
    cases: dict[tuple[str, uuid.UUID], CaseSnapshot] = field(default_factory=dict)

    async def snapshot(self, tenant_id: str, dispute_id: uuid.UUID) -> CaseSnapshot:
        try:
            return self.cases[(tenant_id, dispute_id)]
        except KeyError:
            raise NotFound("dispute", str(dispute_id)) from None

    def add(
        self,
        *,
        tenant: str = "northwind",
        network: str = "VISA",
        code: str = "13.1",
        amount: str = "89.50",
        txn_amount: str | None = None,
        currency: str = "USD",
        three_ds: bool = False,
        avs: str | None = "Y",
        cvv: str | None = "M",
        delivery: str | None = "IN_TRANSIT",
        tracking: str | None = "1Z",
        refunded: str = "0",
        uploads: Sequence[str] = (),
        days_left: float = 20,
        status: DisputeStatus = DisputeStatus.UNDER_REVIEW,
        now: datetime = NOW,
    ) -> uuid.UUID:
        d = Dispute.open(
            tenant_id=tenant,
            reason=lookup(network, code),
            transaction_ref="T-1",
            amount=Money.of(amount, currency),
            transaction_amount=Money.of(txn_amount or amount, currency),
            actor="t",
            now=now - timedelta(days=1),
        )
        d.status, d.respond_by = status, now + timedelta(days=days_left)
        txn = TransactionView(
            ref="T-1",
            amount=Money.of(txn_amount or amount, currency),
            captured_at=now,
            card_brand=network,
            avs_result=avs,
            cvv_result=cvv,
            three_ds_authenticated=three_ds,
            delivery_status=delivery,
            delivery_tracking=tracking,
            refunded=Money.of(refunded, currency),
            description=None,
        )
        ev = tuple(EvidenceItem(uuid.uuid4(), d.id, tenant, k, f"{k} upload", None, "ana", now) for k in uploads)
        self.cases[(tenant, d.id)] = CaseSnapshot(d, txn, ev)
        return d.id


@dataclass
class MemTenantPolicies:
    policies: dict[str, TenantDisputePolicy] = field(
        default_factory=lambda: {
            "northwind": TenantDisputePolicy("northwind", "USD", Money.of("500.00", "USD")),
            "aurora": TenantDisputePolicy("aurora", "EUR", Money.of("750.00", "EUR")),
        }
    )

    async def get(self, tenant_id: str) -> TenantDisputePolicy:
        return self.policies[tenant_id]


@dataclass
class MemThreads:
    rows: dict[str, ThreadRecord] = field(default_factory=dict)
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def start(self, *, tenant_id: str, dispute_id: uuid.UUID, graph: str, started_by: str) -> str:
        if active := await self.active_for(tenant_id, dispute_id):
            raise ResolutionInProgress(str(dispute_id), active)
        n = sum(1 for r in self.rows.values() if r.dispute_id == dispute_id) + 1
        tid = f"{graph}:{dispute_id}:{n}"
        self.rows[tid] = ThreadRecord(tid, tenant_id, dispute_id, graph, "running", started_by, NOW)
        return tid

    def register(self, thread_id: str, tenant_id: str = "northwind", dispute_id: uuid.UUID | None = None) -> None:
        self.rows[thread_id] = ThreadRecord(
            thread_id, tenant_id, dispute_id or uuid.uuid4(), "resolution", "running", "ana", NOW
        )

    async def get(self, tenant_id: str, thread_id: str) -> ThreadRecord:
        r = self.rows.get(thread_id)
        if r is None or r.tenant_id != tenant_id:
            raise NotFound("thread", thread_id)
        return r

    async def set_status(
        self, thread_id: str, status: str, *, outcome: str | None = None, only_from: tuple[str, ...] | None = None
    ) -> bool:
        r = self.rows[thread_id]
        if only_from is not None and r.status not in only_from:
            return False
        self.rows[thread_id] = dataclasses.replace(r, status=status, outcome=outcome or r.outcome)
        return True

    async def active_for(self, tenant_id: str, dispute_id: uuid.UUID) -> str | None:
        return next(
            (
                t
                for t, r in self.rows.items()
                if r.dispute_id == dispute_id and r.status in ("running", "awaiting_approval", "resume_failed")
            ),
            None,
        )

    async def counts(self, graph: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows.values():
            if r.graph == graph:
                out[r.status] = out.get(r.status, 0) + 1
        return out

    @asynccontextmanager
    async def exclusive(self, thread_id: str, *, wait_s: float = 0.0) -> AsyncIterator[None]:
        lock = self.locks.setdefault(thread_id, asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=wait_s or 0.001)
        except TimeoutError:
            raise ThreadBusy(thread_id) from None
        try:
            yield
        finally:
            lock.release()


@dataclass
class MemDisputes:
    calls: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)
    status: dict[uuid.UUID, DisputeStatus] = field(default_factory=dict)

    async def advance(self, *, tenant_id: str, dispute_id: uuid.UUID, goal: str, actor: str, note: str) -> str:
        current = self.status.get(dispute_id, DisputeStatus.UNDER_REVIEW)
        path_to(current, DisputeStatus(goal))  # raises like the real adapter for impossible moves
        self.status[dispute_id] = DisputeStatus(goal)
        self.calls.append((dispute_id, goal, actor))
        return goal


@dataclass
class MemApprovals:
    rows: dict[uuid.UUID, ApprovalRecord] = field(default_factory=dict)

    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        return self.rows.setdefault(record.id, record)

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord:
        r = self.rows.get(approval_id)
        if r is None or r.tenant_id != tenant_id:
            raise NotFound("approval", str(approval_id))
        return r

    async def decide(
        self, *, tenant_id: str, approval_id: uuid.UUID, status: str, actor: str, note: str | None, now: datetime
    ) -> ApprovalRecord:
        r = await self.get(tenant_id, approval_id)
        if r.status != "pending":
            raise ApprovalDecided(r.status)
        if (status != "expired") == (r.expires_at <= now):
            raise ApprovalExpired(str(approval_id))
        r = dataclasses.replace(r, status=status, decided_by=actor, decided_at=now, note=note)
        self.rows[approval_id] = r
        return r

    async def pending(
        self, tenant_id: str, *, roles: Sequence[str], exclude_requester: str, limit: int
    ) -> list[ApprovalRecord]:
        return [
            r
            for r in self.rows.values()
            if r.tenant_id == tenant_id
            and r.status == "pending"
            and r.required_role in roles
            and r.requested_by != exclude_requester
        ][:limit]

    async def due_for_expiry(self, now: datetime, *, limit: int) -> list[ApprovalRecord]:
        return [r for r in self.rows.values() if r.status == "pending" and r.expires_at <= now][:limit]


@dataclass
class MemSideEffects:
    rows: dict[str, SideEffectRecord] = field(default_factory=dict)

    async def reserve(self, record: SideEffectRecord) -> SideEffectRecord:
        return self.rows.setdefault(record.idempotency_key, record)

    async def finish(self, key: str, *, status: str, external_ref: str | None, error: str | None) -> None:
        r = self.rows[key]
        if r.status != "succeeded":
            self.rows[key] = dataclasses.replace(r, status=status, external_ref=external_ref, error=error)

    async def stale_unknown(self, older_than: datetime, *, limit: int) -> list[SideEffectRecord]:
        return [r for r in self.rows.values() if r.status in ("unknown", "pending")][:limit]

    async def open_for(self, tenant_id: str, dispute_id: uuid.UUID) -> list[SideEffectRecord]:
        return [
            r
            for r in self.rows.values()
            if r.tenant_id == tenant_id and r.dispute_id == dispute_id and r.status != "failed"
        ]


@dataclass
class MemAudit:
    rows: list[dict[str, Any]] = field(default_factory=list)

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
        if dedupe_key and any(r.get("dedupe_key") == dedupe_key for r in self.rows):
            return
        self.rows.append(
            {
                "tenant_id": tenant_id,
                "actor": actor,
                "action": action,
                "subject": subject,
                "data": data,
                "dedupe_key": dedupe_key,
            }
        )


@dataclass
class MemOutbox:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def enqueue(self, *, tenant_id: str, type: str, payload: dict[str, object], dedupe_key: str) -> None:
        self.rows.setdefault(dedupe_key, {"tenant_id": tenant_id, "type": type, "payload": payload})


@dataclass
class FakeToolGateway:
    """Behaves like the payments / network MCP servers: dedupes on the idempotency key."""

    transient_failures: int = 0
    permanent_error: str | None = None
    malformed: bool = False
    not_attempted: int = 0  # refused before sending (rate limit / unreachable): attempts 0, retryable
    delay_s: float = 0.0
    calls: list[dict[str, Any]] = field(default_factory=list)
    effects: dict[str, str] = field(default_factory=dict)  # idempotency key -> external ref

    async def call(
        self,
        tool: str,
        args: dict[str, object],
        *,
        tenant_id: str,
        idempotency_key: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolResult:
        self.calls.append({"tool": tool, "args": args, "tenant_id": tenant_id, "key": idempotency_key})
        if self.not_attempted:
            self.not_attempted -= 1
            return ToolResult(ok=False, data={"attempts": 0}, retryable=True, error="rate_limited")
        if self.permanent_error:
            return ToolResult(ok=False, data={}, retryable=False, error=self.permanent_error)
        if self.transient_failures:
            self.transient_failures -= 1
            return ToolResult(ok=False, data={"attempts": 3}, retryable=True, error="timeout")
        assert idempotency_key
        ref = self.effects.setdefault(idempotency_key, f"ext_{len(self.effects) + 1}")
        if self.delay_s:
            await asyncio.sleep(self.delay_s)  # effect already recorded: a crash now leaves the outcome unknown
        if self.malformed:
            return ToolResult(ok=False, data={"attempts": 1}, retryable=True, error="malformed_output: missing ['x']")
        return ToolResult(ok=True, data={"external_ref": ref, "attempts": 1})


def make_services(**overrides: Any) -> GraphServices:
    base: dict[str, Any] = dict(
        clock=FixedClock(),
        policy_store=FakePolicyStore([network_hit(), tenant_hit()]),
        drafter=RuleBasedDrafter(),
        cases=MemCases(),
        tenant_policies=MemTenantPolicies(),
        resolution_drafter=RuleBasedResolutionDrafter(),
        disputes=MemDisputes(),
        threads=MemThreads(),
        approvals=MemApprovals(),
        side_effects=MemSideEffects(),
        tools=FakeToolGateway(),
        audit=MemAudit(),
        outbox=MemOutbox(),
        limits=GraphLimits(),
    )
    base.update(overrides)
    return GraphServices(**base)


def make_ctx(services: GraphServices, *, tenant: str = "northwind", user: str = "ana", role: str = "analyst") -> Ctx:
    return Ctx(tenant_id=tenant, user_id=user, role=role, request_id="test", services=services)


def make_runtime(ctx: Ctx, thread_id: str = "resolution:t:1") -> Runtime[Ctx]:
    return Runtime(
        context=ctx, execution_info=ExecutionInfo(checkpoint_id="c", checkpoint_ns="", task_id="t", thread_id=thread_id)
    )


class FailOnce:
    """Wraps an adapter: raises `exc` on the first `times` calls of `method` whose kwargs match `when`."""

    def __init__(
        self, inner: Any, method: str, exc: Exception, when: dict[str, Any] | None = None, times: int = 1
    ) -> None:
        self.inner, self.method, self.exc, self.when, self.times = inner, method, exc, when or {}, times

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if name != self.method:
            return target

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if self.times and all(kwargs.get(k) == v for k, v in self.when.items()):
                self.times -= 1
                raise self.exc
            return await target(*args, **kwargs)

        return wrapped


TRANSIENT = TransientError("simulated connection drop")
