# Pressure cards — resolution workflow v2 (T02.8)

Built with the pressure-pass method from `docs/spec.md` and `specs/*.feature`. Every cell is HANDLED (named
mechanism + acceptance criterion), DEFERRED (with risk) or N/A (with reason). Ladder tags: L3 pattern,
L4 mechanism, L5 construct, L6 component, L7 code operation.

## 1. Summary

Scope: the `/v2` resolution workflow, its approval loop, execution of refunds/representments, and the v1 assist
endpoint it upgrades. Assumptions: single Postgres per environment; MCP servers run as separate processes the
service reaches over streamable HTTP; one service replica in dev, ≥ 2 in prod (so in-process locks are not enough).

## 2. Operation inventory

| Operation | Actor | State read | State written | External calls | Trust boundary | Sync/async |
|---|---|---|---|---|---|---|
| StartResolution | analyst+ | dispute, txn, evidence, policies, tenant policy, `threads` | checkpoint, `threads`, `approvals`, dispute (O4/O9), `audit_log` | model (optional) | y (JWT → tenant) | sync up to first stop |
| DecideApproval | approver | `approvals`, checkpoint | `approvals`, `audit_log`, checkpoint (resume) | — (execution follows) | y | sync |
| ExpireApprovals | sweeper | `approvals` past TTL | `approvals`, checkpoint, `audit_log` | — | y (system identity) | scheduled |
| ExecuteAction | graph after approval | prepared action, `side_effects` | `side_effects`, `tool_calls` | payments / network-gateway MCP | y (money, network) | sync within resume |
| ApplyResolution + Notify | graph | dispute (version) | dispute + events, `outbox` | — | n | sync |
| ListApprovals | analyst+ | `approvals` | — | — | y | sync |
| GetResolution / Stream | analyst+ | checkpoint, `threads` | — | — | y | sync / SSE |
| ThreadHistory | admin | checkpoints | — | — | y | sync |
| AskAssistV1 (upgraded) | analyst+ | dispute, txn, evidence, policies, v1 checkpoint | checkpoint | model (optional) | y | sync |
| GradeEvidence, VerifyDraft | graph | state only | state only | — | n | pure |

## 3. Triage

- FULL: StartResolution, DecideApproval, ExpireApprovals, ExecuteAction, ApplyResolution + Notify, AskAssistV1.
- LIGHT: ListApprovals, GetResolution / Stream, ThreadHistory.
- SKIP: GradeEvidence, VerifyDraft — pure functions of state; covered by unit tests on the domain rules.

## 4. Pressure cards

### StartResolution — `POST /v2/disputes/{id}/resolutions`

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | HANDLED | [L5] partial unique index `threads(dispute_id) WHERE status IN ('running','awaiting_approval')`; insert before invoke | Two concurrent starts on one dispute → one 202/201, one 409 `resolution_in_progress` |
| Failure | HANDLED | [L4] checkpoint per super-step (`durability="sync"` on resolution graph); `threads.status` updated in the same request; crash leaves a resumable thread | Kill the worker after `grade_evidence` → `POST …/resume` (admin) continues from `draft_resolution` |
| Time | HANDLED | [L4] `asyncio.timeout(APP_RUN_TIMEOUT_S=30)` at API; model timeout 20 s; `respond_by` checked at `load_case` and in verify (R3) | Drafter slower than timeout → 504 `run_timeout`, thread still readable |
| Scale | HANDLED | [L7] `instructions` ≤ 1 000 chars; evidence capped at 12 items after merge; checkpoint stores ids + short excerpts (≤ 400 chars) | 1 001-char instructions → 422; checkpoint blob of a run < 64 KB |
| Trust | HANDLED | [L4] JWT role ≥ analyst; tenant from `Ctx` only; repositories filter `tenant_id`; instructions treated as data (never select tools or roles) | Cross-tenant dispute → 404; G30 injection still requires approval |
| Change | HANDLED | [L3] new `/v2` prefix; graph `schema_version=1` for resolution state; v1 untouched except deprecation header | v1 contract tests unchanged and green |
| Evidence | HANDLED | [L4] `threads` row (who, when, request id); structlog `thread_id` on every node log; `audit_log` `resolution.started` | Given a start, one audit row and node logs carry `thread_id` |

### DecideApproval — `POST /v2/approvals/{id}/decision`

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | HANDLED | [L5] `UPDATE approvals SET status=… WHERE id=:id AND status='pending' RETURNING` — compare-and-set; only the winner resumes | Two simultaneous approves → one 200, one 409 `approval_decided`; one tool call |
| Failure | HANDLED | [L3] decision row committed before `Command(resume=)`; if resume fails the approval stays `approved` and `threads.status='resume_failed'`; admin resume endpoint replays with the stored decision | Crash between commit and resume → resume endpoint finishes the run once |
| Time | HANDLED | [L4] TTL check inside the same CAS (`AND expires_at > now()`) | Decision after TTL → 410 `approval_expired` |
| Scale | N/A | One row per decision; queue size bounded by open disputes | — |
| Trust | HANDLED | [L4] role rank ≥ `required_role`; four-eyes `decided_by ≠ requested_by`; tenant filter; resume value built server-side (never from the body) | Requester approves → 403; analyst on ops_lead item → 403 |
| Change | HANDLED | [L5] decision payload versioned (`{"v":1,"decision":…}`) so later fields (e.g. edited amount) are additive | Resume value without `v` is rejected by the approve node |
| Evidence | HANDLED | [L4] `audit_log` row `approval.approved/rejected` with actor, role, request id, note | Every decision has exactly one audit row |

### ExpireApprovals — sweeper

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | HANDLED | [L5] same CAS as DecideApproval (`status='pending' AND expires_at <= now()`); `FOR UPDATE SKIP LOCKED` batch so replicas don't collide | Sweeper and approver race → exactly one wins |
| Failure | HANDLED | [L3] idempotent: re-running finds nothing pending; resume failures leave `threads.status='resume_failed'` | Sweeper crash mid-batch → next run completes the rest |
| Time | HANDLED | [L4] runs every 5 min; TTL = min(created + 72 h, respond_by − 24 h); DB `now()` is the clock | Approval created 30 h before respond_by expires 6 h later |
| Scale | HANDLED | [L7] batch of 100 per tick | 250 expired approvals → 3 batches |
| Trust | HANDLED | [L4] runs as `system:sweeper`; can only write `expired` | Sweeper cannot approve |
| Change | N/A | Internal job; no external contract | — |
| Evidence | HANDLED | [L4] audit `approval.expired`; metric `approvals_expired_total` | Expiry produces an audit row and increments the metric |

### ExecuteAction — `execute` node → ToolGateway → MCP

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | HANDLED | [L5] `side_effects.idempotency_key` UNIQUE; key = sha256(tenant, dispute, thread, kind, amount) fixed in `prepare_action` (the approval id does not exist yet at that point; one thread = one action); MCP servers dedupe on the same key | Double resume → one `side_effects` row, one ledger refund |
| Failure | HANDLED | [L3] record `side_effects` as `pending` before the call, `succeeded/failed/unknown` after; timeout → `unknown` + reconciliation by key (gateway `get_refund(idempotency_key)`) | Crash after call, before apply → resume reads `succeeded` and skips the call |
| Time | HANDLED | [L4] per-call timeout 4 s; the gateway retries transient errors 3× with the same key; exhausted → `unknown` + reconciliation; node `RetryPolicy` only for `TransientError` from our own DB | Two timeouts then success → 3 attempts, one refund |
| Scale | HANDLED | [L4] gateway rate limit per tenant (token bucket 5/s); `max_concurrency=4` per run | Burst of 20 executions → no 429 from MCP server |
| Trust | HANDLED | [L6] ToolGateway: allow-list per graph node, tenant check on every call, approval id must be `approved` — reason: two MCP servers and shared policy justify a component | Call without approved approval → `PermissionError`, no MCP request |
| Change | HANDLED | [L4] tool contracts pinned by name + schema hash; gateway refuses unknown schema | MCP server changing `issue_refund` schema → gateway error at startup |
| Evidence | HANDLED | [L4] `tool_calls` row per attempt (args hash, latency, result code); OTel span `tool.<name>` | Each attempt has a row; spans nest under the run |

### ApplyResolution + Notify

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | HANDLED | [L5] dispute optimistic `version`; on conflict reload and re-apply if the transition is still valid | Analyst edits dispute during run → apply retries once and succeeds |
| Failure | HANDLED | [L3] transactional outbox: dispute transition + `outbox` row in one transaction; node is idempotent (skips if dispute already resolved with this side-effect ref) | Crash after commit → re-run makes no second transition or message |
| Time | N/A | No external call | — |
| Scale | N/A | One row each | — |
| Trust | HANDLED | [L4] transition actor = approver; tenant from Ctx | Dispute event actor is the approver |
| Change | HANDLED | [L4] outbox message has `type` + `schema_version` | Consumers ignore unknown versions |
| Evidence | HANDLED | [L4] dispute event carries `side_effect_id`; audit `resolution.applied` | Timeline shows the refund id |

### AskAssistV1 (upgraded)

| Pressure | Decision | Mechanism | Acceptance |
|---|---|---|---|
| Concurrency | DEFERRED | Two questions on the same thread at once interleave history | Risk: analyst double-click doubles a turn; low impact. Revisit if v1 traffic stays after M6 |
| Failure | HANDLED | [L4] checkpoint after each step (v1 behaviour) | Unchanged v1 tests green |
| Time | HANDLED | [L4] same API run timeout | Slow drafter → 504 |
| Scale | N/A | History grows one entry per question; v1 is deprecated | — |
| Trust | HANDLED | [L4] tenant from Ctx instead of state | Cross-tenant → 404 (existing test) |
| Change | HANDLED | [L3] `schema_version` upgrade hook (missing → 2); `Deprecation` + `Sunset` headers | v1 fixture resumes with turns + 1 |
| Evidence | HANDLED | [L4] node logs carry `thread_id` | log-capture test |

### LIGHT operations

| Operation | Trust | Scale | Evidence |
|---|---|---|---|
| ListApprovals | HANDLED — tenant filter; only items the caller's role can decide and not requested by the caller | HANDLED — `limit ≤ 100`, `offset`; index `(tenant_id, status, priority, created_at)` | HANDLED — request log |
| GetResolution / Stream | HANDLED — thread looked up through `threads` with tenant filter, never by raw thread id | HANDLED — SSE closes at terminal or after 60 s idle | HANDLED — request log with `thread_id` |
| ThreadHistory | HANDLED — `admin` only, tenant filter | HANDLED — `limit ≤ 50` checkpoints | HANDLED — audit `admin.history_viewed` |

## 5. Introduced-problem chains

- `[L5] threads partial unique index -> introduces: stuck 'running' row after crash blocks new starts -> [L4] admin resume/cancel endpoint + 'running' older than run timeout × 2 treated as stale -> introduces: stale detection races a slow run -> N/A (run timeout bounds any live run to 30 s)`
- `[L3] idempotency key fixed before approval -> introduces: approver changes nothing but key must cover amount -> key includes amount and approval_id -> introduces: edited-amount approvals need a new key -> DEFERRED (no amount editing in v2)`
- `[L5] side_effects pending/unknown -> introduces: unknown rows need reconciliation -> [L4] reconcile by key via gateway lookup in the sweeper -> introduces: gateway lookup unavailable -> alert when unknown > 15 min (stage 25)`
- `[L5] approvals CAS -> introduces: decision committed but resume failed -> threads.status='resume_failed' + admin replay -> N/A (replay is idempotent through side_effects)`
- `[L4] RetryPolicy on tools -> introduces: retries multiply cost/latency -> transient-only predicate, 3 attempts max -> N/A`
- `[L6] ToolGateway -> introduces: one more component to fail -> in-process, no network hop; MCP client sessions pooled in container -> introduces: MCP session drop -> reconnect on next call with timeout -> N/A`
- `[L3] outbox -> introduces: duplicate publish -> consumer dedupes on message id -> DEFERRED (relay + consumer are outside this PR; table only)`
- `[L4] checkpoint ids + short excerpts in state -> introduces: drafter needs full text -> fetch by id through PolicyStore at draft time -> N/A`
- `[L3] schema_version upgrade hook -> introduces: hook must stay forever -> fixtures per release + changelog -> DEFERRED (drop v1 hook after Sunset date + 90 days)`

## 6. Invariants and state

Invariants:
- I1 At most one non-terminal resolution thread per dispute.
- I2 At most one `side_effects` row per idempotency key; at most one `succeeded` refund/representment per approval.
- I3 Sum of succeeded refunds for a transaction ≤ transaction amount.
- I4 No side effect without an `approved` approval whose `decided_by ≠ requested_by` (or `system:auto` under tenant policy).
- I5 Every terminal thread has exactly one `outcome`, written once.
- I6 Every row in `approvals`, `side_effects`, `tool_calls`, `audit_log`, `threads` has a `tenant_id` equal to the dispute's.

Lifecycles:

| Entity | States | Transitions (trigger) | Unknown / failure exit |
|---|---|---|---|
| Thread (`threads.status`) | running → awaiting_approval → running → done; running → done | start (analyst), interrupt (graph), decision/expiry (approver/sweeper), terminal node (graph) | `resume_failed` → admin replay → running; stale `running` → admin cancel → `cancelled` |
| Approval | pending → approved / rejected / expired | decision endpoint; sweeper | none (CAS makes the move atomic) |
| Side effect | pending → succeeded / failed / unknown | execute node | `unknown` → reconcile by key → succeeded / failed |
| Dispute (new transition) | UNDER_REVIEW → EVIDENCE_GATHERING (`request_info`) | graph, actor = requester | — |

## 7. Acceptance criteria

The Gherkin in `specs/*.feature` is the acceptance set; the table maps each HANDLED cell to it.

| Card / cell | Scenario |
|---|---|
| StartResolution · Concurrency | resolution.feature "A second resolution while one awaits approval is refused" |
| StartResolution · Time | isolation_and_compat.feature "A run that exceeds the API timeout…" |
| StartResolution · Trust | isolation_and_compat.feature outline; golden G30 |
| DecideApproval · Concurrency | approval.feature "Two decisions on the same approval…" |
| DecideApproval · Trust | approval.feature "The requester cannot approve…", "…needs an ops lead" |
| DecideApproval / Expire · Time | approval.feature "An approval not decided before its TTL expires" |
| ExecuteAction · Failure | execution.feature "Process crash after the refund but before the dispute update" |
| ExecuteAction · Time | execution.feature "Transient gateway errors are retried" |
| ExecuteAction · permanent failure | execution.feature "Permanent gateway error ends in execution failure" |
| AskAssistV1 · Change | isolation_and_compat.feature "A v1 assist thread continues after the upgrade" |

Cells without a scenario above (StartResolution Scale/Change/Evidence, ExecuteAction Scale/Trust/Change/Evidence,
ApplyResolution all, Expire Concurrency/Scale/Trust/Evidence, LIGHT rows) are covered by unit/integration tests
named in stage 14–16 and listed in the PR traceability table.

## 8. Standing rules

- SR1 Every external call has an explicit timeout and goes through a port; no vendor import in `app/orchestration`.
- SR2 Retries only on reads and on idempotent calls with a key; never on `apply_resolution` without its idempotency check.
- SR3 Identity and tenant come from `Ctx`, never from state or request bodies.
- SR4 Every state-changing endpoint writes an `audit_log` row in the same transaction.
- SR5 Every table carries `tenant_id`; every query filters on it.
- SR6 State holds ids and short excerpts, not documents, clients, secrets or PANs.
- SR7 Every new state field is added to the ownership table and the changelog in the same commit.

## 9. Deferred register

| ID | Item | Risk | Revisit trigger |
|---|---|---|---|
| DF1 | Concurrent v1 assist questions on one thread | Interleaved history entries | v1 still used after Sunset |
| DF2 | Approver edits the amount | Must re-key the side effect | Product asks for it |
| DF3 | Outbox relay + consumer | Notifications queued but not delivered by this service | Notification service integration |
| DF4 | Dropping the v1 schema upgrade hook | Dead code kept | Sunset + 90 days |
| DF5 | Multi-region checkpoint store | Single region outage stops runs | Availability target > 99.9 % |

## 10. Open questions (defaults used)

| Question | Owner | Default |
|---|---|---|
| PARTIAL percentage per tenant? | Product | 50 % fixed in v2; `tenant_dispute_policy.partial_pct` column reserved |
| Should REQUEST_INFO need approval? | Product | No (no money or network action) |
| Approval TTL per tenant? | Product | 72 h global, capped by `respond_by − 24 h` |

Next: run chain-first-codegen with this spec (stage 8, M2).
