# Engineering guarantees — resolution workflow (v2)

Stage 1 output. Each row is a property the change must hold, the LangGraph mechanism that carries part of it,
the control outside LangGraph that closes the gap, the process stages that deliver it, who owns it, and the
artifact that proves it. A row is "done" only when its proof exists and is green in CI.

Owners are roles. In this repo one person holds every role today (Hifzan); the role column is what a reviewer
checks against when the team grows.

| # | Guarantee | LangGraph mechanism | Outside control | Stages | Owner (role) | Proof |
|---|---|---|---|---|---|---|
| G1 | **Responsibility allocation** — every responsibility has exactly one owning layer and module | Nodes coordinate only; services do the work | `import-linter` layer + forbidden contracts; `docs/allocation.md` | 4, 8 | Tech lead | `lint-imports` green in CI; allocation table has no row with two layers |
| G2 | **Contracts at boundaries** — API, graph input/output, tools and ports have typed, versioned contracts | `input_schema`, `output_schema`, `context_schema` | Pydantic API models; `Protocol` ports; MCP tool JSON schemas; ADR-0002 | 4, 5, 12 | Tech lead | contract tests on `/v2` schemas; graph output contains only `Out` keys (`tests/graph/test_contracts.py`) |
| G3 | **Valid shared state** — every state field has one owner or an explicit, order-independent reducer; no identity, client or secret in state | `StateGraph` schema, `Annotated` reducers | Ownership table + `OWNERS` map; reducer property tests | 5, 14 | Graph owner | `tests/unit/test_state_ownership.py`, `tests/unit/test_reducers.py` (hypothesis) |
| G4 | **Governed control flow** — every path ends at a terminal outcome; every loop has a counter, a max and an exit | Conditional edges with path maps, `Command(goto=)`, `recursion_limit` | Loop table and outcome walk in `docs/graph_design.md` | 6, 9 | Graph owner | one graph-scenario test per outcome O1–O11 (`tests/graph/test_outcomes.py`); recursion-limit test |
| G5 | **Recoverable execution** — a crash at any node boundary resumes without losing or repeating work; live v1 assist threads keep working | `AsyncPostgresSaver`, durability mode, `schema_version` upgrade hook | Checkpoint fixtures per release; checkpointer setup as a deploy step | 10, 16, 27 | Platform owner | crash-resume chaos test; `tests/graph/test_checkpoint_compat.py` against `tests/fixtures/checkpoints/v1_assist.json` |
| G6 | **Safe side effects** — refunds and representments happen at most once per approved action, even under retry, resume and double submit | `RetryPolicy` only on read nodes; side-effect nodes after `interrupt` | Idempotency keys + `side_effects` table with a unique key; tool gateway dedupe; reconciliation query | 10, 12, 16 | Payments owner | chaos test "crash after tool call, before apply" shows one `side_effects` row and one ledger refund |
| G7 | **Human control and authorization** — money-moving or network-facing actions need an approval from an allowed role who is not the requester; resume is authorized | `interrupt()` / `Command(resume=)` | `approvals` table, role matrix, four-eyes rule, TTL expiry sweeper, `audit_log` | 11, 13 | Security owner | API tests: wrong role 403, self-approval 403, second decision 409, expired 410; every decision has an audit row |
| G8 | **Tenant isolation** — no read, retrieval, tool call or thread crosses a tenant | `context_schema` carries tenant; thread ids include tenant-scoped dispute ids | Repository filters on `tenant_id`; retrieval filter `tenant_id = :t OR NULL`; gateway checks tenant on every call | 5, 7, 13 | Security owner | isolation tests on every `/v2` route; eval metric `cross_tenant_reads = 0` |
| G9 | **Observable execution** — every run, node, tool call and decision can be traced from a request id | `stream_mode="updates"`, run metadata, callbacks | structlog with `request_id`/`thread_id`; OTel spans; `/metrics`; redaction of sensitive fields | 13, 25 | Platform owner | log-capture test shows `thread_id` on node logs and no raw sensitive values; metrics endpoint scraped in smoke test |
| G10 | **Verified, governed change** — behaviour is proven by tests and evals before merge, and every change is reviewed, reversible and traceable | Graph-level tests with `MemorySaver`; `draw_mermaid` diagram diffs | Golden set + eval baseline; CI gates; independent review; Conventional Commits; rollback runbook | 2, 14–23, 26 | Release owner | CI green on the PR; eval report vs `evals/baseline.json`; PR body traceability table; workbook "Implemented in" column |

## Gap review (T01.5)

Reviewed each row against the question "what can still go wrong if only the LangGraph mechanism is used?".

| Gap found | Row | Resolution |
|---|---|---|
| A checkpoint restores state but does not stop a resumed `execute` node from paying twice | G6 | Idempotency key is computed in `prepare_action` (before the interrupt) and stored in state, so the resumed node reuses it; `side_effects.idempotency_key` is unique |
| `interrupt()` accepts any resume value from any caller | G7 | Resume only through `POST /v2/approvals/{id}/decision`, which checks role, four-eyes and approval status inside one DB transaction before calling `Command(resume=)` |
| After resume the run executes under the approver's identity, so the requester is no longer in `Ctx` | G7 | `requested_by` is graph input and persisted in state; four-eyes compares it with the approver from `Ctx` |
| v1 assist threads were written without `schema_version` | G5 | v2 assist entry node upgrades missing/1 → 2 in place; compatibility test against the committed v1 fixture |
| `saver.setup()` runs on every app boot (v1) and needs DDL rights at runtime | G5 | Moved to an explicit deploy step (`scripts/checkpoint_setup.py`), gated by `APP_CHECKPOINT_AUTO_SETUP` for local dev |
| An approval can sit pending past the network deadline | G7 | TTL = min(created + 72 h, `respond_by` − 24 h); sweeper resumes with `expired` |
| Logs could carry cardholder-facing text and policy excerpts | G9 | `SENSITIVE` map in `state.py`; structlog processor redacts listed keys |
