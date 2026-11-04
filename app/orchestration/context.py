"""Run-scoped context passed to every graph invocation (`context_schema`).

Identity, tenant and request id live here, never in state (SR3): they are not checkpointed, and after an approval
resumes a thread the context is the approver's, not the requester's. Service handles are ports built once in
`bootstrap.container`; nodes reach them through `runtime.context.services`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ports import PolicyStore, RecommendationDrafter


@dataclass(frozen=True, slots=True)
class GraphServices:
    """Process-wide handles the graphs use. Grows with the milestones: M2 adds case reader, resolution drafter,
    tenant policy and thread registry; M3 adds approvals, side-effect ledger, tool gateway, audit log and outbox."""

    policy_store: PolicyStore
    drafter: RecommendationDrafter
    retrieval_top_k: int = 5


@dataclass(frozen=True, slots=True)
class Ctx:
    tenant_id: str
    user_id: str
    role: str
    request_id: str
    services: GraphServices
