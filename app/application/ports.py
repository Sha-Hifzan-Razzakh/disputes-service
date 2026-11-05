"""Ports (interfaces) and the plain data shapes that cross them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.dispute import Dispute
from app.domain.money import Money


@dataclass(frozen=True, slots=True)
class TransactionView:
    ref: str
    amount: Money
    captured_at: datetime
    card_brand: str
    avs_result: str | None
    cvv_result: str | None
    three_ds_authenticated: bool
    delivery_status: str | None
    delivery_tracking: str | None
    refunded: Money
    description: str | None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: uuid.UUID
    dispute_id: uuid.UUID
    tenant_id: str
    kind: str
    description: str
    uri: str | None
    submitted_by: str
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    at: datetime
    actor: str
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class PolicyHit:
    chunk_id: str
    document_title: str
    text: str
    score: float
    scope: str  # "tenant" | "network"


@dataclass(frozen=True, slots=True)
class Recommendation:
    text: str
    suggested_resolution: str
    citations: list[str] = field(default_factory=list)


class DisputeRepository(Protocol):
    async def get(self, tenant_id: str, dispute_id: uuid.UUID) -> Dispute: ...
    async def page(self, tenant_id: str, *, status: str | None, limit: int, offset: int) -> Sequence[Dispute]: ...
    async def add(self, d: Dispute) -> None: ...
    async def save(self, d: Dispute, *, expected_version: int) -> None: ...
    async def timeline(self, tenant_id: str, dispute_id: uuid.UUID) -> list[TimelineEntry]: ...


class EvidenceRepository(Protocol):
    async def add(self, item: EvidenceItem) -> None: ...
    async def for_dispute(self, tenant_id: str, dispute_id: uuid.UUID) -> list[EvidenceItem]: ...


class Ledger(Protocol):
    async def get_transaction(self, tenant_id: str, ref: str) -> TransactionView: ...


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class PolicyStore(Protocol):
    async def add_document(self, *, tenant_id: str | None, title: str, source: str, text: str) -> int: ...
    async def search(self, *, tenant_id: str, query: str, k: int) -> list[PolicyHit]: ...


class RecommendationDrafter(Protocol):
    async def draft(self, *, case: dict[str, object], question: str, hits: list[PolicyHit]) -> Recommendation: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


# --- v2 resolution workflow (stage 4, T04.2) -----------------------------------------------------------------
# Data shapes and ports the resolution graph and its endpoints depend on. Adapters arrive in M2 (persistence)
# and M3 (approvals, tool gateway, MCP clients); the graph only ever sees these Protocols.


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """Everything a run needs about one dispute, read once at the start of the run (tenant-filtered)."""

    dispute: Dispute
    transaction: TransactionView
    evidence: tuple[EvidenceItem, ...]


@dataclass(frozen=True, slots=True)
class TenantDisputePolicy:
    tenant_id: str
    currency: str
    approval_threshold: Money  # refunds at or above need ops_lead
    auto_approve_below: Money | None = None  # None = auto-approve off (default)
    partial_pct: int = 50


@dataclass(frozen=True, slots=True)
class DraftResolution:
    resolution: str  # ACCEPT | PARTIAL | CONTEST | REQUEST_INFO
    amount: Money | None
    rationale: str
    citations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: uuid.UUID
    tenant_id: str
    dispute_id: uuid.UUID
    thread_id: str
    requested_by: str
    required_role: str
    priority: str  # normal | urgent
    status: str  # pending | approved | rejected | expired
    summary: dict[str, object]
    created_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SideEffectRecord:
    idempotency_key: str
    tenant_id: str
    dispute_id: uuid.UUID
    kind: str  # refund | representment
    status: str  # pending | succeeded | failed | unknown
    external_ref: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, object]
    retryable: bool = False
    error: str | None = None


class TransientError(Exception):
    """Raised by adapters for failures that are safe to retry (dropped connection, lock timeout, 5xx).
    Graph nodes attach `RetryPolicy(retry_on=TransientError)`; nothing else is retried automatically."""


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    thread_id: str
    tenant_id: str
    dispute_id: uuid.UUID
    graph: str
    status: str  # running | awaiting_approval | done | failed | resume_failed | cancelled
    started_by: str
    created_at: datetime
    outcome: str | None = None
    graph_version: str | None = None  # release + state schema that started the thread (T26.2)


class CaseReader(Protocol):
    async def snapshot(self, tenant_id: str, dispute_id: uuid.UUID) -> CaseSnapshot: ...


class TenantPolicyRepository(Protocol):
    async def get(self, tenant_id: str) -> TenantDisputePolicy: ...


class ResolutionDrafter(Protocol):
    async def draft(
        self, *, case: dict[str, object], evidence: list[dict[str, object]], feedback: list[str], instructions: str
    ) -> DraftResolution: ...


class ThreadRegistry(Protocol):
    async def start(
        self, *, tenant_id: str, dispute_id: uuid.UUID, graph: str, started_by: str, graph_version: str = "unknown"
    ) -> str:
        """Register a new thread and return its id; raises ResolutionInProgress if one is active."""
        ...

    async def get(self, tenant_id: str, thread_id: str) -> ThreadRecord: ...
    async def set_status(
        self, thread_id: str, status: str, *, outcome: str | None = None, only_from: tuple[str, ...] | None = None
    ) -> bool:
        """Set the status (only when the current one is in `only_from`, if given). True if a row changed."""
        ...

    async def active_for(self, tenant_id: str, dispute_id: uuid.UUID) -> str | None: ...
    async def counts(self, graph: str) -> dict[str, int]:
        """Threads of one graph by status, across tenants (operations metric)."""
        ...

    def exclusive(self, thread_id: str, *, wait_s: float = 0.0) -> AbstractAsyncContextManager[None]:
        """Hold the thread for one invocation across all replicas, waiting up to `wait_s` for another holder;
        raises ThreadBusy after that."""
        ...


class DisputeCommands(Protocol):
    async def advance(self, *, tenant_id: str, dispute_id: uuid.UUID, goal: str, actor: str, note: str) -> str:
        """Move the dispute to `goal` along the allowed transitions; a no-op when already there. Returns status."""
        ...


class ApprovalRepository(Protocol):
    async def create(self, record: ApprovalRecord) -> ApprovalRecord:
        """Insert if absent (id is derived from the thread), return the stored row either way."""
        ...

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord: ...
    async def decide(
        self, *, tenant_id: str, approval_id: uuid.UUID, status: str, actor: str, note: str | None, now: datetime
    ) -> ApprovalRecord:
        """Compare-and-set from pending; raises ApprovalDecided / ApprovalExpired."""
        ...

    async def pending(
        self, tenant_id: str, *, roles: Sequence[str], exclude_requester: str, limit: int
    ) -> list[ApprovalRecord]: ...
    async def due_for_expiry(self, now: datetime, *, limit: int) -> list[ApprovalRecord]: ...


class SideEffectLedger(Protocol):
    async def reserve(self, record: SideEffectRecord) -> SideEffectRecord:
        """Insert as pending, or return the existing row for the key (never a second row)."""
        ...

    async def finish(self, key: str, *, status: str, external_ref: str | None, error: str | None) -> None: ...
    async def stale_unknown(self, older_than: datetime, *, limit: int) -> list[SideEffectRecord]: ...
    async def open_for(self, tenant_id: str, dispute_id: uuid.UUID) -> list[SideEffectRecord]:
        """Side effects of this dispute that may have moved money: pending, unknown or succeeded."""
        ...


class AuditLog(Protocol):
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
    ) -> None: ...


class Outbox(Protocol):
    async def enqueue(self, *, tenant_id: str, type: str, payload: dict[str, object], dedupe_key: str) -> None: ...


class ToolGateway(Protocol):
    """Single door to external side-effecting and lookup tools (payments, network gateway).

    Enforces the tool allow-list, tenant match, approval status, idempotency and rate limits before any MCP call.
    """

    async def call(
        self,
        tool: str,
        args: dict[str, object],
        *,
        tenant_id: str,
        idempotency_key: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolResult: ...
