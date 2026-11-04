# Runtime, resources and dependencies (stage 7)

Every process-wide resource is created once in `app/bootstrap/container.py`, lives on `app.state.container`,
and is closed by the lifespan in reverse order. No node or router creates a client (`lint-imports` contract
"Graph code never touches vendors, drivers or the container").

| Resource | Created | Limits | Closed | Used by |
|---|---|---|---|---|
| SQLAlchemy async engine (psycopg 3) | `build_container` | `APP_DB_POOL_SIZE` (5) + overflow 5, pre-ping | `engine.dispose()` | repositories, policy store |
| Checkpointer pool (`psycopg_pool.AsyncConnectionPool`) | `build_container` | 1–`APP_CHECKPOINT_POOL_MAX` (4), autocommit, `prepare_threshold=0` | `pool.close()` | `AsyncPostgresSaver` |
| `AsyncPostgresSaver` | lifespan | `setup()` only when `APP_CHECKPOINT_AUTO_SETUP=true` (dev/test; refused in staging/prod) | with pool | compiled graphs |
| Shared `httpx.AsyncClient` | `build_container` | timeout `APP_HTTP_TIMEOUT_S` (10 s, connect 3 s), 20 connections | `aclose()` | MCP transport in the tool gateway (M3) |
| Chat model (only when `APP_MODEL_PROVIDER=anthropic`) | `build_drafter` | timeout 20 s, `max_retries` 2, `InMemoryRateLimiter` 2 rps burst 5 | — (stateless) | `AnthropicDrafter` |
| Compiled graphs | lifespan, once: `evidence` (no checkpointer of its own; runs under the parent task), `assist`, `resolution` | — | with container | runners |
| Workflow adapters (`SqlCaseReader`, `SqlThreadRegistry`, `SqlDisputeCommands`, `SqlApprovalRepository`, `SqlSideEffectLedger`, `SqlAuditLog`, `SqlOutbox`, `SqlTenantPolicyRepository`) | `build_container` | one short transaction per call; DB connection errors → `TransientError` | — | `GraphServices` |
| `GraphServices` | `build_container` | — | — | `Ctx.services` |

## Context on every invoke (T07.8)

`api/deps.py::graph_context` builds `Ctx(tenant_id, user_id, role, request_id, services)` from the JWT principal
and the request-id middleware; runners pass it as `context=` and put tenant, user and request id into run
`metadata` for tracing. Nodes take `runtime: Runtime[Ctx]`.

## Limits (T07.9)

| Limit | Setting | Default | Enforced in |
|---|---|---|---|
| API run timeout | `APP_RUN_TIMEOUT_S` | 30 s → 504 `run_timeout` | `api/deps.py::bounded_run` |
| Recursion limit | `APP_RECURSION_LIMIT` | 40 | `orchestration/runners.py::run_config` |
| Branch concurrency | `APP_MAX_CONCURRENCY` | 4 | same |
| Tool call timeout / attempts | `APP_TOOL_TIMEOUT_S`, `APP_TOOL_MAX_ATTEMPTS` | 4 s / 3 | tool gateway (M3) |
| Tools kill switch | `APP_TOOLS_ENABLED` | false | `build_tool_gateway` → `DisabledToolGateway` |
| Checkpoint retention | `APP_CHECKPOINT_RETENTION_DAYS` | 90 | `scripts/prune_checkpoints.py` |

## Secrets per environment (T07.2)

All secrets are `SecretStr` and come from the environment (`APP_*`), never from files in the image.
`Settings` refuses to start in `staging`/`prod` with the dev JWT secret or with checkpointer auto-setup on, and
refuses `APP_MODEL_PROVIDER=anthropic` without `APP_ANTHROPIC_API_KEY` (v1 silently fell back to rules).
