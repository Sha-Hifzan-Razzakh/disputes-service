# State changelog (T05.8)

One entry per state schema version. Every release that changes a state commits a checkpoint fixture
(`tests/fixtures/checkpoints/`) and a test that resumes it.

## assist

### v2 — this PR
- Added: `schema_version` (written by `upgrade_state`), `queries`, `retrieval_attempts`, `evidence`,
  `evidence_grade` — all written by the wrapped `gather_evidence` node and replaced per turn (M2 change from the
  M1 draft, which used `merge_evidence` here and would have carried evidence across turns).
- Kept with same reducer: `history` (`operator.add`).
- Legacy, still declared, never read: `tenant_id` (tenant comes from `Ctx`), `policy_hits` (replaced by `evidence`).
- Upgrade: missing `schema_version` → treat as 1 → set 2; no other rewrite.
- Fixture: `tests/fixtures/checkpoints/v1_assist.json` (v1.4.0) must resume.

### v1 — 1.4.0 (implicit, no `schema_version`)
`tenant_id, dispute, transaction, evidence_kinds, question, reason_family, policy_hits, recommendation,
suggested_resolution, citations, history`.

## resolution

### v1 — this PR
Initial: `dispute_id, requested_by, instructions, schema_version, case, reason_family, queries,
retrieval_attempts, evidence, evidence_grade, draft, verification, refine_attempts, action, approval, execution,
outcome, outcome_reason`. Reducers: `outcome`, `outcome_reason` → `write_once`; `evidence` replaces (single
writer `gather_evidence`). Shapes: `EvidenceGrade.can_retry`, `Verification.can_refine` and `Action.auto` let the
routers stay pure functions of state. A resolution checkpoint fixture is exported with the first release that
ships the approvals endpoints (M3), when a paused thread can be resumed through the public contract.

## evidence (subgraph, checkpointed under the parent task)

### v1 — this PR
`network, reason_code, reason_family, question, facts, queries, retrieval_attempts, evidence (merge_evidence),
evidence_grade`.
