# Graph topology design (stage 6, T06.1–T06.7)

Diagram: `docs/graph_target.mmd`. Spec references: `docs/spec.md` D1–D8, O1–O11.

## Edge list with path maps (T06.2)

Routers are pure functions of state. A decision that needs I/O, the clock or a resume value is made inside a
node and returned as `Command(goto=…)` (T06.3).

| From | Kind | Decision function | Path map |
|---|---|---|---|
| `load_case` | `Command` (needs DB + clock) | node body | `close_expired` if `now ≥ respond_by`; else `classify_reason` |
| `plan_queries` (subgraph) | conditional → `Send` | `fan_out_queries(state)` | one `Send("retrieve_policy", {"query": q, …})` per query (1–3) + `"gather_txn_evidence"` |
| `grade_evidence` (subgraph) | conditional | `route_after_grade(state)` | `{"sufficient": END, "retry": "plan_queries", "exhausted": END}` |
| `gather_evidence` (parent) | conditional | `route_after_evidence(state)` | `{"sufficient": "draft_resolution", "insufficient": "escalate_insufficient"}` |
| `verify` | conditional | `route_after_verify(state)` | `{"passed": "prepare_action", "refine": "draft_resolution", "failed": "escalate_unverified"}` |
| `prepare_action` | conditional | `route_approval(state)` | `{"request_info": "request_info", "approval": "request_approval"}` |
| `approve` | `Command` (needs resume value) | node body after `interrupt()` | `execute` (approved/auto) · `record_rejection` · `record_expiry` |
| `execute` | conditional | `route_after_execute(state)` | `{"succeeded": "apply_resolution", "failed": "record_failure"}` |
| all terminal nodes | static | — | `notify` → END |

Why `Command` only at `load_case` and `approve`: those are the only decisions that depend on something outside
state (wall clock, human decision). Everything else is a router so it can be unit-tested as a pure function
and rendered with a labelled path map.

Urgency is not a node: `prepare_action` computes `action.priority` and `required_role` (domain
`approval_policy`), and `request_approval` uses them. The first plan's `escalate_urgent` node is dropped because
it changed no flow, only data.

## Parallelism (T06.4)

| Where | Kind | Join | Writers | Reducer |
|---|---|---|---|---|
| `plan_queries` → `retrieve_policy` × N | `Send` fan-out (N = number of planned queries, 1–3) | `grade_evidence` (next super-step) | `retrieve_policy` instances | `merge_evidence` |
| `plan_queries` → `gather_txn_evidence` | static branch in the same super-step | `grade_evidence` | `gather_txn_evidence` | `merge_evidence` |

`max_concurrency = 4` caps branch concurrency per run. Both branches write only `evidence`; nothing else is
written in parallel. `merge_evidence` is commutative, associative and idempotent (property tests).

## Subgraphs (T06.5)

| Subgraph | Used by | Boundary | Why a subgraph |
|---|---|---|---|
| evidence (`EvidenceState`) | assist v2, resolution | **Wrapped**: parent node `gather_evidence` maps parent state → `EvidenceState` and back | Same retrieval/grade/retry logic for both graphs; parents have different state shapes (`dispute`+`transaction` vs `case`), so a shared-key subgraph would force one shape on both |

The wrapped subgraph is compiled once, invoked inside the node with the parent's config so its checkpoints sit
under the parent thread's namespace. Approval and execution stay in the parent: they need `requested_by`,
`action` and the outcome reducer, and splitting them would only add mapping code.

## Loops (T06.6)

| Loop | Counter | Max | Exit | Owner of counter |
|---|---|---|---|---|
| `grade_evidence → plan_queries` | `retrieval_attempts` | 2 rounds (`APP_MAX_RETRIEVAL_ATTEMPTS`) | subgraph END with `sufficient=false` → `escalate_insufficient` | `plan_queries` |
| `verify → draft_resolution` | `refine_attempts` | 1 refine (`APP_MAX_REFINE_ATTEMPTS`) | `escalate_unverified` | `verify` |
| tool retry | inside the ToolGateway with the same idempotency key (not a graph cycle); a server crash (`Error executing tool …`) is retryable, a malformed reply from a side-effecting tool is `unknown` (the effect may have happened → reconciled by key), from a read-only tool it is a permanent error | 3 (`APP_TOOL_MAX_ATTEMPTS`) | `execution.status = unknown` → `record_failure` | gateway |
| node retry on `TransientError` | `RetryPolicy` on idempotent nodes (load_case, close_expired, draft_resolution, prepare_action, request_info, request_approval, execute, apply_resolution, notify, retrieve_policy) | 3 | exception → thread `failed` → recover | LangGraph runtime |

No other cycles: `approve` resumes exactly once per interrupt (a second decision is refused by the approvals
CAS before it can reach the graph).

**Recursion limit.** LangGraph counts the input step, so a path of n node super-steps needs a limit of n + 1.
Longest single invocation: O6 (`load_case, classify_reason, gather_evidence, draft_resolution, verify,
draft_resolution, verify, escalate_unverified, notify`) = 9 → needs 10; the approval path with one refine is 10
→ 11; the evidence subgraph runs ≤ 6 inside its own task. Resume: `approve, execute, apply_resolution, notify` = 4.
`recursion_limit = 40` (`APP_RECURSION_LIMIT`) leaves ~4× headroom. `test_worst_path_fits_the_recursion_budget`
pins it (10 passes, 9 aborts); an abort surfaces as 500 `run_aborted`.

**Built vs designed (M2).** `scripts/draw_graphs.py` exports `docs/graph_after_resolution.mmd` and
`docs/graph_after_assist.mmd` from the compiled graphs; node set, path-map labels and the single `notify` exit
match `graph_target.mmd`. `tests/graph/test_contracts.py::test_compiled_topology_matches_design` asserts the node
set and reachability. Nodes read the thread id from `runtime.execution_info` (no `config` parameter).

## Every outcome walked to a terminal (T06.7)

| Outcome | Path |
|---|---|
| O1 REFUNDED | load_case → classify_reason → gather_evidence → draft_resolution → verify → prepare_action → request_approval → approve ⏸ → execute → apply_resolution → notify → END |
| O2 PARTIALLY_REFUNDED | same as O1 with `draft.resolution = PARTIAL` |
| O3 REPRESENTED | same as O1 with CONTEST; `execute` calls `network.submit_representment` |
| O4 INFO_REQUESTED | … → verify → prepare_action → request_info → notify → END |
| O5 ABSTAINED_INSUFFICIENT_EVIDENCE | … → gather_evidence (2 rounds) → escalate_insufficient → notify → END |
| O6 ABSTAINED_FAILED_VERIFICATION | … → draft_resolution → verify → draft_resolution → verify → escalate_unverified → notify → END |
| O7 REJECTED | … → approve ⏸ → record_rejection → notify → END |
| O8 APPROVAL_EXPIRED | … → approve ⏸ (sweeper resumes `expired`) → record_expiry → notify → END |
| O9 DEADLINE_PASSED | load_case → close_expired → notify → END |
| O10 EXECUTION_FAILED | … → approve ⏸ → execute (retries) → record_failure → notify → END |
| O11 AWAITING_APPROVAL | pause at `approve` ⏸; thread `awaiting_approval` until O1/O2/O3/O7/O8/O10 |

No unreachable node: every node above appears in at least one path. `notify` is the single exit, so the
outbox write for every outcome lives in one place.
