# Error taxonomy (T08.1, T09.1)

Expected business results are **state** (`outcome` + `outcome_reason`), never exceptions. Exceptions are for
callers that broke a rule, systems that failed, and bugs. Only one class is retried automatically.

| Class | Raised by | Retried? | Becomes | Example |
|---|---|---|---|---|
| Business outcome | graph terminal nodes | — | `outcome` (O1–O10), HTTP 201/202 | ABSTAINED_INSUFFICIENT_EVIDENCE, EXECUTION_FAILED |
| `DomainError` | domain (`Money`, `Dispute`, rules) | no | 422 `rule_violation` | amount over transaction |
| `InvalidTransition` | `Dispute.apply`, `path_to`, runner start guard | no | 409 `invalid_transition` | resolution on a withdrawn dispute (A2) |
| `VersionConflict` | repositories | once inside `SqlDisputeCommands.advance`, else no | 409 `version_conflict` | concurrent edit |
| `NotFound` | repositories, thread registry | no | 404 `not_found` | other tenant's dispute or thread (A6) |
| `ResolutionInProgress` | thread registry (partial unique index) | no | 409 `resolution_in_progress` | second start (A1) |
| `ApprovalDecided` / `ApprovalExpired` | approvals CAS | no | 409 / 410 | double decision (A4), late decision (A5) |
| **`TransientError`** | adapters (`transaction()` wraps `OperationalError`, invalidated connections) | **yes** — `RetryPolicy(max_attempts=3, backoff 0.2 s × 2, retry_on=TransientError)` on nodes whose effects are idempotent | 5xx only if all attempts fail | dropped DB connection |
| Tool transient failure | MCP transport (M3) | yes, **inside the gateway** with the same idempotency key | `execution.status = unknown` → EXECUTION_FAILED + reconciliation | payments timeout |
| Tool permanent failure | tool result | no | `execution.status = failed` → EXECUTION_FAILED | card account closed |
| `InvalidResume` | `approve` node | no | 500 (a server-built resume value was wrong — bug) | four-eyes violated, malformed value |
| `RunAborted` | runner (wraps `GraphRecursionError`) | no | 500 `run_aborted`, error log with `alert=true`, thread `failed` | loop bug |
| `ThreadBusy` | `ThreadRegistry.exclusive` (advisory lock per thread) | no | 409 `thread_busy` | two resumes/recovers of one thread at once |
| `SideEffectUnsettled` | `ResolutionRunner.start` | after reconciliation | 409 `side_effect_unsettled` | a refund/representment for the dispute is pending, unknown or succeeded (review R1) |
| `FeatureDisabled` | start route, `Settings.resolution_enabled_for` | no | 403 `resolution_not_enabled` | tenant not in the rollout |
| `TooManyRuns` | API `bounded_run` admission (`APP_MAX_CONCURRENT_RUNS`) | client, after `Retry-After` | 429 `too_many_runs` | overload |
| `TimeoutError` | API `bounded_run` | no | 504 `run_timeout`; thread keeps its last checkpoint | slow model |
| anything else | — | no | 500, thread `failed`, recoverable with `ResolutionRunner.recover` | bug |

`app.application.errors.is_retryable(exc)` is the single predicate; graph nodes never catch broad exceptions.
