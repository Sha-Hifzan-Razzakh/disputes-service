"""Request/response DTOs for the /v2 resolution workflow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StartResolutionIn(BaseModel):
    instructions: str = Field(default="", max_length=1000, description="Context for the drafter; never changes rules")


class DraftOut(BaseModel):
    resolution: str
    amount: str | None
    currency: str
    rationale: str
    citations: list[str]


class ApprovalStateOut(BaseModel):
    approval_id: str | None
    status: str | None
    required_role: str | None
    priority: str | None
    expires_at: str | None
    decided_by: str | None


class ExecutionOut(BaseModel):
    status: str
    external_ref: str | None
    attempts: int
    error: str | None


class ResolutionOut(BaseModel):
    thread_id: str
    dispute_id: str
    status: str = Field(description="running | awaiting_approval | done | failed | resume_failed | cancelled")
    outcome: str | None
    outcome_reason: str | None
    draft: DraftOut | None
    approval: ApprovalStateOut | None
    execution: ExecutionOut | None
    next: list[str] = Field(description="Nodes that run next; empty when the thread is finished")

    @classmethod
    def of(cls, v: dict[str, Any]) -> ResolutionOut:
        return cls.model_validate(v)


class ApprovalOut(BaseModel):
    id: str
    dispute_id: str
    thread_id: str
    requested_by: str
    required_role: str
    priority: str
    status: str
    summary: dict[str, Any]
    summary_hash: str = Field(description="Send back with the decision; a mismatch means the screen is stale (409)")
    created_at: str
    expires_at: str
    decided_by: str | None
    note: str | None

    @classmethod
    def of(cls, r: Any) -> ApprovalOut:
        from app.application.approvals import summary_hash

        return cls(
            id=str(r.id),
            dispute_id=str(r.dispute_id),
            thread_id=r.thread_id,
            requested_by=r.requested_by,
            required_role=r.required_role,
            priority=r.priority,
            status=r.status,
            summary=r.summary,
            summary_hash=summary_hash(r.summary),
            created_at=r.created_at.isoformat(),
            expires_at=r.expires_at.isoformat(),
            decided_by=r.decided_by,
            note=r.note,
        )


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=1000)
    summary_hash: str = Field(min_length=64, max_length=64)


class DecisionOut(BaseModel):
    approval: ApprovalOut
    resolution: ResolutionOut
