# ADR-0004: Side-effecting tools behind a ToolGateway, served over MCP

- Status: Accepted · Date: 2026-11-04 · Stage: 4, 12

## Context
Refunds and representments move money or talk to networks. Model output must never decide *whether* a call is
allowed; the payments and network teams own their APIs and want one contract for every consumer.

## Options
1. Call vendor SDKs from nodes. 2. Application services per tool. 3. One `ToolGateway` port; tools exposed by
MCP servers (`payments`, `network-gateway`) owned next to the systems they wrap.

## Decision
Option 3. The graph only knows `ToolGateway.call(tool, args, tenant_id, idempotency_key, approval_id)`. The
gateway (infrastructure) enforces: per-node allow-list, tenant match, approval status `approved`, idempotency
key present for side effects, per-tenant rate limit, timeout, retry on transient errors, a `tool_calls` row per
attempt. MCP servers dedupe on the idempotency key as a second line.

## Consequences
- One component to test for authorization and idempotency (G6, G7); nodes stay thin.
- Extra hop and a process to run per server; in tests the gateway talks to the servers in-process.
- Tool schema changes are caught at startup by schema-hash pinning.
- Reason for component-level strategy (pressure-card rule): two servers, three tools, shared policy.
