# Human-in-the-loop approvals (stage 11)

## Approval matrix (T11.1)

| Action | Needs approval? | Required role | Urgent (< 48 h to respond_by) | Auto-approve |
|---|---|---|---|---|
| ACCEPT (refund disputed amount) | yes | `analyst` below tenant threshold, `ops_lead` at or above (inclusive) | at least `ops_lead`, priority `urgent` | only below `tenant_dispute_policy.auto_approve_below`, never urgent (off by default) |
| PARTIAL (refund part) | yes | same as ACCEPT, on the partial amount | same | same |
| CONTEST (representment) | yes | `analyst` | `ops_lead`, `urgent` | never |
| REQUEST_INFO | no — no money or network action | — | — | — |

Every decision also requires: same tenant, decider ≠ requester (four-eyes; `admin` included), approval still
`pending` and `expires_at > now()`. Tenant thresholds are seeded from each merchant's policy document
(northwind 500 USD, aurora 750 EUR).

## Prepare → approve → execute (T11.2, T11.3)

`prepare_action` fixes amount, tool and idempotency key; `request_approval` stores the `approvals` row (id = uuid5 of
the thread, so replays insert nothing new) and marks the thread `awaiting_approval`; `approve` pauses:

```json
interrupt payload  {"v": 1, "approval_id": "…", "required_role": "ops_lead", "priority": "normal",
                    "expires_at": "…", "summary": {"resolution": "ACCEPT", "amount": "640.00", "currency": "USD", "rationale": "…"}}
resume value       {"v": 1, "approval_id": "…", "decision": "approved|rejected|expired", "decided_by": "olu", "note": null}
```

The resume value is built by the server from the stored row (`application/approvals.py::resume_value`); clients
never send it. `approve` re-validates it (version, approval id, decision, four-eyes) as defence in depth.

## Endpoints (T11.5, T11.10)

| Method | Path | Notes |
|---|---|---|
| POST | `/v2/disputes/{id}/resolutions` | 202 + approval (id, role, priority, expiry) when the run pauses |
| GET | `/v2/approvals?limit=` | queue: pending items whose role the caller satisfies, excluding their own requests, urgent first |
| GET | `/v2/approvals/{id}` | detail incl. `summary` and `summary_hash` |
| POST | `/v2/approvals/{id}/decision` | `{decision: approve|reject, note?, summary_hash}` → decision + resolution state after the resume |
| POST | `/admin/threads/{id}/recover` | admin: replay a stored decision whose resume failed, or continue a failed run |
| POST | `/admin/threads/{id}/cancel` | admin: stop a stuck thread so a new resolution can start |
| GET | `/v2/disputes/{id}/resolutions/{thread_id}/stream` | SSE `state` events until terminal or idle |

## Single use, stale payloads, expiry, audit (T11.6–T11.9)

| Concern | Mechanism | Proof |
|---|---|---|
| Two deciders at once | `UPDATE approvals … WHERE status='pending' AND expires_at > now() RETURNING` — one row wins; loser 409 `approval_decided` | `test_two_simultaneous_decisions_one_wins_one_side_effect` |
| Decision on a changed summary | client sends `summary_hash` it was shown; mismatch → 409 `approval_changed` | `test_role_four_eyes_and_stale_payload_are_refused` |
| Wrong role / requester | 403 `forbidden` before any write | same test |
| Expiry | sweeper: CAS to `expired`, resume with `expired` → `APPROVAL_EXPIRED`; later decisions → 410 | `test_expiry_by_sweeper_then_late_decision_is_410` |
| Decision stored but resume failed | thread `resume_failed`; admin recover replays the stored decision once | `test_resume_failure_keeps_the_decision_and_admin_recover_replays_it` |
| Audit | `resolution.started`, `approval.requested`, `approval.approved/rejected/expired/auto`, `resolution.completed`, `admin.*` rows with actor, request id; deduplicated per approval/thread | `test_approved_refund_runs_end_to_end_with_the_payments_server` |
