# Changelog

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
