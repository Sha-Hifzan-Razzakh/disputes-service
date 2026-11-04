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
