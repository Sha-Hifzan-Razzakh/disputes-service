# Rollout, deploy notes and rollback (v2.0.0, v2.1.0)

Automated since 2.1.0: `.github/workflows/deploy.yml` runs these steps (Kubernetes staging via
`scripts/deploy_k8s.sh`, AWS prod via `scripts/deploy_ecs.sh`); the operator view is `docs/runbook.md`.
Per-environment differences: `docs/environments.md`.

## Deploy order

| Step | Command | Notes |
|---|---|---|
| 1 | `alembic upgrade head` | `0002` resolution workflow tables (threads, tenant_dispute_policy, approvals, side_effects, outbox, audit_log) → `0003` tool_calls → `0004` threads.graph_version (2.1.0, server default for the previous release). Additive only: safe before the new code. |
| 2 | `python scripts/checkpoint_setup.py` | LangGraph checkpointer tables (idempotent). `APP_CHECKPOINT_AUTO_SETUP` must be false in staging/prod. |
| 3 | Seed tenant dispute policy | one `tenant_dispute_policy` row per tenant (approval threshold, partial %, auto-approve limit). Required: without a row the run stops at `load_case` (404 `not_found`). |
| 4 | Deploy MCP servers | `mcp_servers.payments`, `mcp_servers.network_gateway`, each with its bearer token. |
| 5 | Pin check | automatic at app start (`connect_tools`): live tool schemas vs `PINNED_SCHEMAS`; a mismatch disables only that tool and logs `alert=true`. Check the start logs. |
| 6 | Deploy the app | the settings validator refuses unsafe combinations at start (secrets, pool vs run slots, tools without tokens). |
| 7 | Schedule jobs | daily `scripts/prune_checkpoints.py`; `scripts/sweep.py` only if the in-process sweeper is off. |

## Flags and their defaults

| Setting | dev / test | staging | prod (initial) | Effect |
|---|---|---|---|---|
| `APP_RESOLUTION_TENANTS` | unset → all | `*` | unset → none, then one pilot tenant, then `*` | who may **start** a resolution; nothing else is gated |
| `APP_TOOLS_ENABLED` | false (tests use fakes) | true | false until start logs show no pin mismatch, then true | kill switch: false → approved actions end `EXECUTION_FAILED` with no call |
| `APP_MODEL_PROVIDER` | rules | rules | rules (LLM after a nightly LLM-eval pass at threshold) | drafter |
| `APP_SWEEPER_ENABLED` | true | true | true | expiry + reconciliation |

Rollout: deploy dark (flag unset) → pilot tenant → watch `resolution` dashboard and alerts for a week
(`ops/dashboards/resolution.json`, `ops/alerts/disputes.rules.yml`) → `*`.

## Rollback

| Situation | Action | Pending work |
|---|---|---|
| Resolution behaviour wrong | remove the tenant from `APP_RESOLUTION_TENANTS` (config only) | pending approvals stay decidable; running threads finish |
| A tool misbehaves | `APP_TOOLS_ENABLED=false` | approved actions end `EXECUTION_FAILED` with their idempotency key; re-run after the fix via a new resolution |
| The release itself | redeploy the previous image (v1.4.0). **Do not downgrade the schema**: v1 ignores the new tables. | v2 resolution threads pause untouched and continue when v2 is redeployed |

### Drill (run before this release)

`scripts/rollback_drill.py` against one database, new → previous → new code:

```
migrated to 0003 (head)
write: assist answered (v2 graph), resolution paused for approval resolution:9f917d1d-…:1
previous release: 2462be0 ci: GitHub Actions, Dockerfile, compose and pre-commit
read: previous release answered a follow-up on the v2 thread: ['citations', 'recommendation', 'suggested_resolution', 'turns']
forward: paused resolution decided after the round trip -> EXECUTION_FAILED
```

The previous release answers on an assist thread whose checkpoint carries v2 keys (it ignores undeclared channels),
and the resolution paused across the round trip is still decided. `EXECUTION_FAILED` is the kill switch (tools off
in the drill), not a failure of the drill.
