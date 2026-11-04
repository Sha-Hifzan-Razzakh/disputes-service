"""Run-scoped context passed to every graph invocation (`context_schema`).

Identity, tenant and request id live here, never in state (SR3): they are not checkpointed, and after an approval
resumes a thread the context is the approver's, not the requester's. Service handles are ports built once in
`bootstrap.container`; nodes reach them through `runtime.context.services`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.application.ports import (
    ApprovalRepository,
    AuditLog,
    CaseReader,
    Clock,
    DisputeCommands,
    Outbox,
    PolicyStore,
    RecommendationDrafter,
    ResolutionDrafter,
    SideEffectLedger,
    TenantPolicyRepository,
    ThreadRegistry,
    ToolGateway,
)


@dataclass(frozen=True, slots=True)
class GraphLimits:
    retrieval_top_k: int = 5
    min_evidence_score: float = 0.12
    max_retrieval_attempts: int = 2
    max_refine_attempts: int = 1
    approval_ttl: timedelta = timedelta(hours=72)
    urgent_window: timedelta = timedelta(hours=48)
    approval_deadline_margin: timedelta = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class GraphServices:
    clock: Clock
    policy_store: PolicyStore
    drafter: RecommendationDrafter  # v1/v2 assist
    cases: CaseReader
    tenant_policies: TenantPolicyRepository
    resolution_drafter: ResolutionDrafter
    disputes: DisputeCommands
    threads: ThreadRegistry
    approvals: ApprovalRepository
    side_effects: SideEffectLedger
    tools: ToolGateway
    audit: AuditLog
    outbox: Outbox
    limits: GraphLimits

    @property
    def retrieval_top_k(self) -> int:  # kept for v1 call sites
        return self.limits.retrieval_top_k


@dataclass(frozen=True, slots=True)
class Ctx:
    tenant_id: str
    user_id: str
    role: str
    request_id: str
    services: GraphServices
