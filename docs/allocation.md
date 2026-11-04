# Responsibility allocation (T04.3)

Every responsibility from the spec has exactly one owning layer and module. The layer rules are encoded in
`[tool.importlinter]` (pyproject.toml) and checked in CI by `lint-imports`.

```
app.main
app.api                                   HTTP: auth, validation, status codes, run timeout, SSE
app.bootstrap                             composition root: settings, pools, clients, compile, lifespan
app.orchestration | app.infrastructure    graph coordination  |  adapters (DB, retrieval, model, MCP)
app.application                           use cases + ports (Protocols) + data shapes
app.domain                                aggregate, rules, money — no I/O
```

Forbidden on top of the layers: orchestration → any driver/vendor/`fastapi`; api → `app.infrastructure`,
`langgraph`; application/domain → `langgraph`, `langchain_core`, `sqlalchemy`, `fastapi`, `httpx`, `mcp`.

| # | Responsibility | Layer | Module | Milestone |
|---|---|---|---|---|
| 1 | Authenticate JWT, derive principal, role rank | api | `api/deps.py` | v1 |
| 2 | Build `Ctx` from principal + request id | api | `api/deps.py::graph_context` | M1 |
| 3 | Enforce API run timeout → 504 | api | `api/deps.py::bounded_run` | M1 |
| 4 | Map errors to the envelope (409/410/504 …) | api | `api/errors.py` | v1 + M1/M3 |
| 5 | Refuse a second active resolution per dispute (A1) | application | `application/resolutions.py` (uses `ThreadRegistry`) | M2 |
| 6 | Check dispute is resolvable (A2) | domain | `domain/dispute.py::can_start_resolution` | M2 |
| 7 | Load the case snapshot, tenant-filtered | application | `application/case.py::CaseService` | M1 |
| 8 | Decide deadline passed (D1) | orchestration (node, uses clock) | `orchestration/resolution/nodes.py::load_case` | M2 |
| 9 | Classify reason family | domain | `domain/reason_codes.py::lookup` | v1 |
| 10 | Plan retrieval queries / broaden on retry | orchestration | `orchestration/evidence/nodes.py::plan_queries` | M2 |
| 11 | Embed + search policies with tenant filter | infrastructure | `infrastructure/retrieval.py::SqlPolicyStore` | v1 |
| 12 | Turn ledger + merchant evidence into evidence items | application | `application/evidence.py::ledger_evidence` | M2 |
| 13 | Evidence sufficiency rule per family (D2) | domain | `domain/rules.py::grade_evidence` | M2 |
| 14 | Decide retry vs abstain after grading | orchestration | `orchestration/evidence/routing.py::route_after_grade` | M2 |
| 15 | Draft a resolution (rules or LLM) | infrastructure (adapter) behind `ResolutionDrafter` | `infrastructure/drafting.py` | M2 |
| 16 | Verification rules R1–R6 | domain | `domain/rules.py::verify_draft` | M2 |
| 17 | Decide refine vs abstain | orchestration | `orchestration/resolution/routing.py::route_after_verify` | M2 |
| 18 | Required role, priority, TTL, idempotency key (D6) | domain | `domain/approval_policy.py` | M2 |
| 19 | Decide approval route (auto / request_info / approve / urgent) | orchestration | `orchestration/resolution/routing.py::route_approval` | M2 |
| 20 | Pause for approval and branch on the decision | orchestration | `orchestration/resolution/nodes.py::approve` (`interrupt` + `Command`) | M3 |
| 21 | Persist approval, CAS decision, four-eyes, expiry | application + infrastructure | `application/approvals.py` + `infrastructure/approvals.py` | M3 |
| 22 | Expire approvals past TTL | application (job) | `application/approvals.py::expire_due` | M3 |
| 23 | Policy, allow-list, tenant, rate limit, idempotency for tools | infrastructure | `infrastructure/tools/gateway.py::ToolGateway` | M3 |
| 24 | Talk MCP to payments / network gateway | infrastructure | `infrastructure/tools/mcp_client.py` | M3 |
| 25 | Payments and network MCP servers (simulated) | separate processes | `mcp_servers/payments.py`, `mcp_servers/network_gateway.py` | M3 |
| 26 | Apply the domain transition with optimistic concurrency | application | `application/services.py::DisputeService.act` | v1 |
| 27 | Outbox + audit writes | infrastructure behind ports | `infrastructure/outbox.py`, `infrastructure/audit.py` | M3 |
| 28 | Compile graphs once; checkpointer; pools; clients | bootstrap | `bootstrap/lifespan.py`, `bootstrap/container.py` | M1 |
| 29 | Run config: thread id, recursion limit, concurrency | orchestration | `orchestration/runners.py` | M1 |
| 30 | v1 → v2 assist state upgrade | orchestration | `orchestration/assist.py::upgrade_state` | M2 |
| 31 | Logs/traces/metrics, redaction of sensitive keys | bootstrap (wiring) + api (middleware) | `bootstrap/observability.py` | M3 |

Shared-looking items that were split: "evidence sufficiency" is a pure domain rule (13) while "what to do
when it fails" is a routing decision (14); "approval" is a graph pause (20) plus a persisted record with its
own concurrency control (21).
