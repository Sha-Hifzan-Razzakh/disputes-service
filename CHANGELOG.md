# Changelog

## 2.1.0 — 2026-11-05

### Added
- **Deploy**: Kubernetes manifests (`deploy/k8s`, staging on kind in CI) and AWS production in Terraform
  (`infra/terraform`: ECS Fargate, RDS, ALB with WAF, Secrets Manager). `deploy.yml` builds once, deploys the same
  digest, smoke-tests across two replicas and rehearses both rollback paths on every merge.
- **Run**: SLOs with burn-rate alerts (`ops/slo`), runbook link on every alert, Alertmanager routing, synthetic
  monitor, online evaluation and failure capture (`evals/online.py`), model cost per tenant.
- **Maintain**: `threads.graph_version`, release checkpoints resumed in CI, schema-drift and deprecation gates,
  monthly refresh issue, quarterly review tooling, PR change types, four team skills.
- **Concern table** (`docs/concerns.md`, generated from `ops/concerns.py`).

### Fixed
- MCP servers answered 421 to in-cluster calls (DNS-rebinding protection allowed only localhost).

### Migrations
`0004` threads.graph_version. Additive, with a server default; run before deploying.


## 2.0.0 — 2026-11-04

### Added
- **Resolution workflow** (`/v2/disputes/{id}/resolutions`): a LangGraph run classifies the dispute, gathers ledger
  and policy evidence, drafts a resolution (rules by default, Claude with `APP_MODEL_PROVIDER=anthropic`), verifies
  it against rules R1–R6, routes it for approval, then refunds or submits a representment exactly once.
- **Approvals** (`/v2/approvals`): role by amount and urgency, four-eyes, TTL with a near-deadline window,
  `summary_hash` against stale screens, SSE stream, expiry sweeper.
- **Governed tools**: payments and network-gateway MCP servers behind a deny-by-default gateway with pinned schemas,
  tenant binding, approvals bound to one exact call, idempotency keys, rate limits and a `tool_calls` log.
- **Recovery**: per-thread advisory lock, `durability="sync"`, side-effect ledger with reconciliation, admin
  `history` / `recover` / `cancel`.
- **Operations**: OpenTelemetry traces, Prometheus `/metrics`, JSON logs with redaction, dashboard and alert rules,
  admission control (429 `too_many_runs`), rollout flag `APP_RESOLUTION_TENANTS`, tools kill switch.
- **Verification**: unit, graph, integration, chaos and load suites; 189-case golden set with an independent
  oracle and a release gate; key-gated promptfoo, Ragas and LangSmith jobs.

### Changed
- `/v1/disputes/{id}/assist` runs on the shared evidence subgraph (assist state v2; v1 threads upgrade on their
  next turn) and returns `Deprecation`, `Sunset` and a successor `Link`. Contract unchanged.

### Migrations
`0002` resolution workflow tables → `0003` tool calls log. Additive; run before deploying. See `docs/rollout.md`.

### Rollback
Flag off → tools off → previous image (v1.4.0) without a schema downgrade (drill: `scripts/rollback_drill.py`).

## 1.4.0
- v1 dispute API and the LangGraph assist graph with a Postgres checkpointer.
