# ADR-0001: Run the resolution workflow inside the existing FastAPI service

- Status: Accepted
- Date: 2026-11-04
- Stage: 1 (T01.4)

## Context

- The disputes service is an existing FastAPI app with JWT auth, tenant-scoped repositories, Alembic and one
  LangGraph graph (v1 assist) already running on `AsyncPostgresSaver` in the same Postgres database.
- The new workflow moves money (refunds) and submits representments to card networks. Approvals, audit rows,
  dispute transitions and side-effect records must commit in the same database as the dispute aggregate.
- Dispute and ledger data must stay inside the merchant platform's own infrastructure.
- Human approval needs role checks that already exist in the API layer.

## Options

| Criterion | A. Self-hosted: FastAPI + `AsyncPostgresSaver` | B. LangGraph Platform (managed or self-hosted server) |
|---|---|---|
| Fits the existing service | Same process, same auth, same DB transactions | Second runtime; auth and tenant checks duplicated in a custom auth handler |
| Data residency | Stays in our Postgres | Managed: leaves our infra; self-hosted server: adds a component to operate |
| HITL | `interrupt` + our approvals table and endpoints | Built-in threads/interrupt API, but approvals and four-eyes still ours |
| Cron (approval expiry sweeper) | Own scheduler / ops job | Built-in crons |
| Ops skills | Team already runs this stack | New deployment model |
| Cost | No new licence | Licence or hosting cost |

## Decision

Option A. The workflow runs in the existing service, compiled once at startup with `AsyncPostgresSaver`
sharing the service's Postgres.

## Consequences

- Stage 7: we own pools, the compiled graph's lifetime and shutdown (lifespan), run timeouts and concurrency caps.
- Stage 10: we own checkpointer DDL (`setup()` becomes a deploy step), retention (pruning old checkpoints),
  thread-id design and schema upgrades for live threads.
- Stage 11: we build the approval queue, decision endpoint and the expiry sweeper ourselves.
- Stage 13: tracing and metrics come from our OTel/structlog setup, not a hosted UI; LangGraph Studio is used
  only locally against `langgraph.json`.
- Stage 24: deployment is the existing container image; no extra runtime to operate.
- Revisit if we need many independent graphs owned by different teams, or long-running background agents with
  their own scaling.
