# ADR-0005: Approvals = graph interrupt + approvals table + server-side resume

- Status: Accepted · Date: 2026-11-04 · Stage: 11

## Context
`interrupt()` pauses a thread and `Command(resume=value)` continues it, but LangGraph does not know roles,
four-eyes, expiry, double clicks or audit.

## Decision
- `request_approval` node writes an `approvals` row (pending, required role, priority, TTL, summary) and the
  `approve` node calls `interrupt({"v": 1, "approval_id": …})`.
- Clients never send resume values. `POST /v2/approvals/{id}/decision` performs, in one transaction: tenant
  check, role rank ≥ required, `decided_by ≠ requested_by`, CAS `pending → approved/rejected` with
  `expires_at > now()`, audit row. Only after commit does it call `Command(resume={"v": 1, "decision": …,
  "decided_by": …})` under the approver's `Ctx`.
- The expiry sweeper uses the same CAS to `expired` and resumes with `{"decision": "expired"}`.
- `approve` branches with `Command(goto=…)`; the approval row, not the checkpoint, is the source of truth.

## Consequences
- Double decisions are impossible (CAS); a crash between commit and resume leaves `threads.status =
  resume_failed`, fixed by the admin resume endpoint replaying the stored decision.
- The idempotency key is fixed before the interrupt, so a resumed `execute` cannot create a second side effect.
