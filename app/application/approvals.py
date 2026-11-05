"""Approval decisions (stage 11): who may decide, single use, stale-payload protection, expiry, audit.

The graph pause is only a pause; this service is the source of truth (ADR-0005). The resume value is always built
here from the stored record, never taken from the client.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.application.errors import ApprovalChanged, Forbidden
from app.application.ports import ApprovalRecord, ApprovalRepository, AuditLog, Clock
from app.domain.roles import ROLE_RANK, satisfies

SWEEPER = "system:sweeper"


def summary_hash(summary: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def resume_value(rec: ApprovalRecord) -> dict[str, Any]:
    return {"v": 1, "approval_id": str(rec.id), "decision": rec.status, "decided_by": rec.decided_by, "note": rec.note}


@dataclass
class ApprovalService:
    approvals: ApprovalRepository
    audit: AuditLog
    clock: Clock

    async def get(self, tenant_id: str, approval_id: uuid.UUID) -> ApprovalRecord:
        return await self.approvals.get(tenant_id, approval_id)

    async def queue(self, *, tenant_id: str, user_id: str, role: str, limit: int) -> list[ApprovalRecord]:
        """Pending items the caller could decide: role high enough, and not their own request."""
        roles = [r for r in ROLE_RANK if satisfies(role, r)]
        return await self.approvals.pending(tenant_id, roles=roles, exclude_requester=user_id, limit=limit)

    async def decide(
        self,
        *,
        tenant_id: str,
        approval_id: uuid.UUID,
        user_id: str,
        role: str,
        decision: str,
        note: str | None,
        seen_summary_hash: str,
        request_id: str,
    ) -> ApprovalRecord:
        rec = await self.approvals.get(tenant_id, approval_id)  # other tenant → NotFound (404)
        if not satisfies(role, rec.required_role):
            raise Forbidden(f"approval requires role {rec.required_role}")
        if user_id == rec.requested_by:
            raise Forbidden("four-eyes: the requester cannot decide their own resolution")
        if seen_summary_hash != summary_hash(rec.summary):
            raise ApprovalChanged("the approval summary changed since it was displayed; reload and decide again")
        status = {"approve": "approved", "reject": "rejected"}[decision]
        decided = await self.approvals.decide(
            tenant_id=tenant_id, approval_id=approval_id, status=status, actor=user_id, note=note, now=self.clock.now()
        )  # CAS: ApprovalDecided (409) / ApprovalExpired (410)
        await self.audit.record(
            tenant_id=tenant_id,
            actor=user_id,
            action=f"approval.{status}",
            subject=f"approval:{approval_id}",
            request_id=request_id,
            data={"role": role, "thread_id": rec.thread_id, "note": note},
            dedupe_key=f"approval.decided:{approval_id}",
        )
        return decided

    async def expire_due(self, *, now: datetime | None = None, limit: int = 100) -> list[ApprovalRecord]:
        from app.application.errors import ApprovalDecided, ApprovalExpired

        now = now or self.clock.now()
        expired: list[ApprovalRecord] = []
        for rec in await self.approvals.due_for_expiry(now, limit=limit):
            try:
                done = await self.approvals.decide(
                    tenant_id=rec.tenant_id, approval_id=rec.id, status="expired", actor=SWEEPER, note=None, now=now
                )
            except (ApprovalDecided, ApprovalExpired):
                continue  # an approver won the race
            await self.audit.record(
                tenant_id=rec.tenant_id,
                actor=SWEEPER,
                action="approval.expired",
                subject=f"approval:{rec.id}",
                request_id="sweeper",
                data={"thread_id": rec.thread_id, "expires_at": rec.expires_at.isoformat()},
                dedupe_key=f"approval.decided:{rec.id}",
            )
            expired.append(done)
        return expired
