# Environments (T24.5, T24.7)

Every difference is listed here and set in exactly one place. Anything not listed is identical.

| | dev | CI tests | staging | prod |
|---|---|---|---|---|
| Runs on | laptop / `compose.yaml` | GitHub runner, Postgres service | kind cluster on the runner (`deploy.yml`) | AWS ECS Fargate + RDS (`infra/terraform`) |
| Defined in | `compose.yaml`, `.env` | `ci.yml`, `tests/conftest.py` | `deploy/k8s/base` + `overlays/staging` | `infra/terraform` + `envs/prod.tfvars` |
| Image | built locally | none (source) | GHCR `:<sha>` digest, loaded into kind | same digest, copied to ECR |
| `APP_ENV` | dev | test | staging | prod |
| Database | local Postgres, no TLS | service container | in-cluster Postgres 16, TLS (`sslmode=require`) | RDS Postgres 16, Multi-AZ, `rds.force_ssl=1`, private subnets |
| Migrations | `alembic upgrade head` by hand | per test session | `migrate` Job before rollout | `migrate` ECS task before rollout |
| Checkpointer setup | auto (`APP_CHECKPOINT_AUTO_SETUP=true`) | auto | migrate Job | migrate task |
| Secrets | `.env` (dev values) | test constants | generated per deploy run | Secrets Manager (KMS), written by a person |
| `APP_RESOLUTION_TENANTS` | unset → all | unset → all | `*` | unset → none, then pilot, then `*` |
| `APP_TOOLS_ENABLED` | true (compose) | false (fakes) | true (in-cluster MCP servers) | false until no pin mismatch at start |
| MCP servers | compose services | in-memory / one HTTP test | Deployments, reachable from the API only | ECS services via Service Connect, reachable from the API only |
| Model | rules | rules | rules | rules; `anthropic` after a passing nightly LLM eval |
| Replicas (API) | 1 | — | 2 (cross-replica resume is smoked) | 3 |
| Egress | open | open | default-deny NetworkPolicies: DNS, Postgres, MCP only | security groups: DB/MCP by group; HTTPS out via NAT (AWS APIs, model provider) |
| Sweeper | in-process | off | in-process (every replica; CAS + thread lock) | in-process |
| Telemetry | none | in-memory spans | `/metrics` | `/metrics`; `APP_OTEL_EXPORTER=otlp` once a collector sidecar exists |

## Connection math (T24.7)

Per API process: SQLAlchemy pool `APP_DB_POOL_SIZE + APP_DB_MAX_OVERFLOW` plus the checkpointer pool
`APP_CHECKPOINT_POOL_MAX`. Each running thread holds one pooled connection for its advisory lock, so settings refuse
`APP_MAX_CONCURRENT_RUNS > pool + overflow − 2` (`docs/capacity.md`).

| Env | API processes | per process | total | Postgres limit |
|---|---|---|---|---|
| staging | 2 | 8 + 4 + 4 = 16 | 32 + migrate/cron ≈ 36 | `max_connections` 100 (image default) |
| prod | 3 (up to 6 during a deploy at 200 %) | 10 + 4 + 4 = 18 | 108 + jobs ≈ 112 | db.r7g.large ≈ 1 700 (RDS formula) |
