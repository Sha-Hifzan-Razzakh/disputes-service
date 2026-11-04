# ADR-0006: Assist v2 keeps v1 thread ids and upgrades state in place

- Status: Accepted · Date: 2026-11-04 · Stage: 3, 10

## Context
Live `assist:<dispute_id>` threads exist (see docs/impact.md). Spec M8: the v1 endpoint keeps its contract and
threads keep their history. v2 reuses the evidence subgraph inside the assist graph.

## Options
1. New thread ids for v2 (history lost). 2. Offline migration rewriting checkpoints. 3. Same ids; the first node
upgrades `schema_version` (missing → 2); legacy keys stay declared.

## Decision
Option 3. `history` keeps `operator.add`. `tenant_id` and `policy_hits` stay in `AssistStateV2` but are never
read (tenant comes from `Ctx`) or written. Each release commits a checkpoint fixture; the compat test loads
every fixture into a fresh checkpointer and runs one turn.

## Consequences
- No downtime migration; old threads upgrade lazily on their next question.
- The legacy keys stay until v1 Sunset + 90 days (deferred register DF4).
- The resolution graph uses a separate `resolution:` namespace, so it never meets v1 state.
