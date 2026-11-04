# Orientation — the v1 service as found (T03.1, T03.6, T03.7)

Discovery run against `main@2462be0` (v1.4.0). Everything here describes v1; the target is in
`docs/allocation.md` and `docs/graph_design.md`.

## Layout and history

| Path | Holds | Entry points |
|---|---|---|
| `app/main.py` | `create_app()` — routers, middleware, error handlers, lifespan | `uvicorn app.main:app` |
| `app/api/` | `deps.py` (JWT principal, role check, DB session, service factories), routers, schemas, error envelope, request-id middleware | HTTP |
| `app/application/` | `DisputeService`, `PolicyService`, `AssistService`, ports (Protocols + data shapes) | called from routers |
| `app/domain/` | `Dispute` aggregate + `TRANSITIONS`, reason-code catalogue, `Money` | — |
| `app/infrastructure/` | SQLAlchemy models + repositories, `SqlPolicyStore` + hashing embedder, drafters | — |
| `app/orchestration/assist.py` | v1 `StateGraph` (3 nodes) | compiled in lifespan |
| `app/bootstrap/` | `Settings`, `Container` + module-global `get_container()`, lifespan | FastAPI lifespan |
| `migrations/` | Alembic `0001_initial_schema` | `alembic upgrade head` |
| `seeds/` | 2 tenants, 12 transactions, network + merchant policies, 6 disputes | `python -m seeds.load` |
| `scripts/` | `mint_token.py`, `export_checkpoint.py` | CLI |

History: 8 commits, scaffold → domain → db → seeds → api → assist graph → tests → CI. One graph, one
checkpointer, no feature flags. Library versions: langgraph 1.2.19, langgraph-checkpoint 4.3.1,
langgraph-checkpoint-postgres 3.2.0 (checkpoint migrations at v9).

## How the graph is built, compiled and invoked

1. `lifespan` builds the `Container`, creates `AsyncPostgresSaver(container.checkpoint_pool)`, runs
   `saver.setup()` (DDL) **on every boot**, compiles `build_assist_graph()` once, stores it on the container
   and publishes the container through a module global (`set_container`).
2. `POST /v1/disputes/{id}/assist` → `assist_service` dependency builds `AssistService` with request-scoped
   repositories and the compiled graph (`graph: Any`).
3. `AssistService.ask` loads dispute, transaction and evidence (tenant-filtered), builds the full input dict,
   and calls `graph.ainvoke(state, {"configurable": {"thread_id": "assist:<dispute_id>"}})`.
   No `context`, no `recursion_limit` (default 25), no timeout, default durability.
4. Nodes call `get_container()` to reach the policy store, drafter and settings.

## Behaviour notes from stepping the graph (T03.6)

- Every call sends the whole input, so `dispute`, `transaction`, `evidence_kinds`, `question` are overwritten
  each turn; only `history` (`operator.add`) accumulates. `turns = len(history)`.
- `tenant_id` travels **in state** and is persisted in every checkpoint; `retrieve_policy` reads it from state.
- `recommendation`, `suggested_resolution`, `citations`, `policy_hits` from the previous turn stay in the
  checkpoint until the next turn overwrites them.
- No conditional edges, no loops, no interrupts; the run always takes 3 steps (checkpoint `step` 3 in the fixture).
- Drafter output is not checked: a recommendation with zero citations is returned as-is.

## Identity, tenant and correlation sources (T03.7)

| Value | Source | Where it goes |
|---|---|---|
| user id | JWT `sub` → `Principal.user_id` | dispute events `actor`; not passed to the graph |
| tenant | JWT `tenant` → `Principal.tenant_id` | repository filters; graph **state** (`tenant_id`) |
| role | JWT `role` → `require_role(min)` with rank analyst < ops_lead < admin | route guards only |
| request id | `X-Request-ID` header or uuid4 in middleware → `request.state.request_id`, structlog contextvar, response header | logs and error envelope; not passed to the graph |
| thread id | `assist:<dispute_id>` built in `AssistService` | checkpointer |

Errors: domain exceptions map to the envelope in `app/api/errors.py` (404 / 409 / 422); there is no 504 path.
