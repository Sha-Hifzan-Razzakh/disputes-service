# Disputes Service

Multi-tenant service for handling card-payment disputes (chargebacks). Each merchant is a tenant; its
analysts open disputes against ledger transactions, gather evidence, and resolve them before the
card network's response deadline. A LangGraph **dispute assistant** answers analyst questions with a
recommended resolution grounded in network-rule summaries and the merchant's own policies.

## Domain

| Concept | Notes |
|---|---|
| Dispute | Aggregate with lifecycle `OPENED → EVIDENCE_GATHERING → UNDER_REVIEW → RESOLVED_ACCEPTED / RESOLVED_CONTESTED`, plus `WITHDRAWN`, `EXPIRED`. Optimistic concurrency via `version`. |
| Reason codes | Visa 10.4, 13.1, 13.3, 12.6; Mastercard 4837, 4853, 4834 — grouped into FRAUD, NOT_RECEIVED, NOT_AS_DESCRIBED, PROCESSING_ERROR with allowed resolutions. |
| Deadlines | Response window per network (illustrative defaults: Visa 30 days, Mastercard 45 days). |
| Ledger | Read-only mirror of the payments ledger (`transactions` table): AVS/CVV, 3-D Secure, delivery tracking, refunds. |
| Policies | Network-rule summaries (shared) + per-tenant merchant policies, chunked and embedded for retrieval. |
| Roles | `analyst` < `ops_lead` < `admin`, carried in the JWT with `tenant` and `sub`. |

## API (v1)

| Method | Path | Role |
|---|---|---|
| POST | `/v1/disputes` | analyst |
| GET | `/v1/disputes?status=&limit=&offset=` | analyst |
| GET | `/v1/disputes/{id}` | analyst |
| PATCH | `/v1/disputes/{id}` (assign / start_evidence / submit_for_review / resolve_accept / resolve_contest) | analyst |
| POST | `/v1/disputes/{id}/withdraw?expected_version=` | analyst |
| GET | `/v1/disputes/{id}/timeline` | analyst |
| POST, GET | `/v1/disputes/{id}/evidence` | analyst |
| POST | `/v1/disputes/{id}/assist` | analyst |
| GET | `/v1/transactions/{ref}` | analyst |
| POST | `/v1/policies/documents` | ops_lead (tenant scope), admin (network scope) |
| GET | `/healthz`, `/readyz` | — |

## API (v2 — resolution workflow)

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/v2/disputes/{id}/resolutions` | analyst | 201 terminal outcome, 202 awaiting approval, 409 in progress / not resolvable |
| GET | `/v2/disputes/{id}/resolutions/{thread_id}` | analyst | status, outcome, draft, approval, execution, next nodes |
| GET | `/admin/threads/{thread_id}/history` | admin | checkpoint trail without values; audited |

`/v1/disputes/{id}/assist` keeps its contract and now returns `Deprecation`, `Sunset` and a successor `Link`.

Errors use one envelope: `{"error": {"code", "message", "request_id"}}`.

## Layout

```
app/
  api/            routers, auth (JWT principal), request context, error envelope
  application/    use cases (DisputeService, PolicyService, AssistService) and ports
  domain/         Dispute aggregate, reason codes, Money — no I/O
  infrastructure/ SQLAlchemy models and repositories, retrieval, drafters
  orchestration/  LangGraph graphs, state contracts, reducers, run context (Ctx), runners
  bootstrap/      settings, container (composition root), FastAPI lifespan
migrations/       Alembic
seeds/            demo tenants, ledger, policies, disputes
tests/            unit, api, graph
```

## Run locally

```bash
uv sync
createdb disputes                      # Postgres 16 on localhost:5432
uv run alembic upgrade head
uv run python scripts/checkpoint_setup.py   # deploy step; dev can rely on APP_CHECKPOINT_AUTO_SETUP=true
uv run python -m seeds.load
uv run uvicorn app.main:app --reload
uv run python scripts/mint_token.py --tenant northwind --role analyst   # bearer token for curl
```

`APP_MODEL_PROVIDER=rules` (default) uses the deterministic drafter; set `anthropic` plus
`APP_ANTHROPIC_API_KEY` to draft with Claude.

## Test

```bash
uv run pytest            # needs local Postgres; creates and drops database disputes_test
uv run ruff check . && uv run mypy app && uv run lint-imports
```

Layer rules (`[tool.importlinter]` in `pyproject.toml`): api → bootstrap → (orchestration | infrastructure) →
application → domain. Graph code never imports drivers, vendors or the container; the API never names an adapter.

Ops scripts: `scripts/checkpoint_setup.py` (deploy), `scripts/prune_checkpoints.py` (daily retention),
`scripts/export_checkpoint.py` (release fixtures), `scripts/draw_graphs.py` (compiled-graph diagrams).

## Design docs (resolution workflow v2)

`docs/guarantees.md` · `docs/spec.md` · `specs/*.feature` · `evals/golden_v0.jsonl` · `docs/pressure_cards.md` ·
discovery (`docs/orientation.md`, `docs/state_inventory.md`, `docs/coupling.md`, `docs/impact.md`) ·
design (`docs/allocation.md`, `docs/c4/`, `docs/adr/`, `docs/graph_design.md`, `docs/graph_target.mmd`,
`docs/runtime.md`, `docs/state_changelog.md`) · build (`docs/errors.md`, `docs/recovery.md`,
`docs/graph_after_*.mmd`).
