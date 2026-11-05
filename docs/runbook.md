# Runbook (T24.12, T25.8)

For whoever is on call. Every alert links to a section here. Commands assume `kubectl` pointed at staging or the
AWS CLI with the prod role; `$NS` is the namespace (`disputes-staging`), `$CLUSTER` the ECS cluster
(`disputes-prod`). Background: `docs/rollout.md` (deploy order, flags), `docs/recovery.md` (thread recovery),
`docs/environments.md` (what differs per environment).

## Deploy

Every merge to `main` runs `.github/workflows/deploy.yml`:

1. **build** — one image, pushed to GHCR as `:<sha>`; its digest is what every later step uses.
2. **staging** (kind cluster on the runner) — `scripts/deploy_k8s.sh`: migrate Job (waited for) → workloads →
   `rollout status`; smoke across two replicas; egress check; the two rollback rehearsals below. Timings are in
   the job summary.
3. **prod** — only from `main`, only when `vars.AWS_DEPLOY_ROLE_ARN` and `vars.CERTIFICATE_ARN` are set, after the
   `production` environment's reviewers approve (configure them in GitHub → Settings → Environments):
   `scripts/deploy_ecs.sh prod <digest>`: copy the same digest to ECR → migrate task (exit 0 required) →
   services (circuit breaker on) → each service must run the task definition just applied (a circuit-breaker
   rollback fails the job) → synthetic check through the load balancer. The first deploy creates the ECR
   repository before copying the image.

To deploy by hand (same path): `scripts/deploy_k8s.sh deploy/k8s/overlays/staging disputes-service:<sha>` or
`scripts/deploy_ecs.sh prod ghcr.io/sha-hifzan-razzakh/disputes-service@sha256:<digest>`.

## Rollback

Fastest first; each step is enough on its own if it fixes the symptom.

| Step | Staging (Kubernetes) | Prod (ECS) | Time |
|---|---|---|---|
| 1. Stop new work | `kubectl -n $NS set env deploy/api APP_RESOLUTION_TENANTS=` | set `APP_RESOLUTION_TENANTS` to `""` in `app_settings`, `terraform apply` | ~2 min |
| 2. Stop money moving | `kubectl -n $NS set env deploy/api APP_TOOLS_ENABLED=false` | same via `app_settings` | ~2 min |
| 3. Previous release | `kubectl -n $NS rollout undo deploy/api` (and the MCP deployments if they changed) | `scripts/deploy_ecs.sh prod <previous digest>` (from the last green deploy run) | staging ≤ 2 min (rehearsed every deploy); prod ~8 min |

Never downgrade the schema: migrations are additive and the previous release ignores the new tables
(drill: `scripts/rollback_drill.py`). Pending approvals stay decidable through every step.

**Automatic rollbacks** (rehearsed on every deploy in staging):
- a release that never becomes ready → `rollout status` times out → `rollout undo` (ECS: deployment circuit
  breaker with rollback);
- a release that is ready but broken → the smoke suite fails → `rollout undo` → smoke again.

## Secrets

Never in the repo, the image or plain env files.
- **Staging:** generated per deploy run (`deploy.yml`, step "secrets"), masked in logs, gone with the cluster.
- **Prod:** Secrets Manager containers `disputes/prod/{database_url,jwt_secret,mcp_payments,mcp_network,anthropic_key}`
  created by Terraform; values are written by a person:
  `aws secretsmanager put-secret-value --secret-id disputes/prod/jwt_secret --secret-string "$(openssl rand -hex 32)"`.
  The RDS master password is managed and rotated by RDS; create a least-privilege `app` role for `database_url`.
- **Rotating the JWT key:** tokens signed with the old key fail at once. Rotate in a quiet window, then restart the
  service (`aws ecs update-service --force-new-deployment`).
- **Rotating an MCP token:** set the new value on both sides (`mcp_payments` is read by the API and the payments
  server), then force a new deployment of both services.

## Execution failures

`ResolutionExecutionFailures`: an approved refund or representment ended `EXECUTION_FAILED`.
1. Find the thread: `outcome_reason` names the error and, for unknown outcomes, the idempotency key.
2. `tool_calls` rows for that key show every attempt (`status`, `error`).
3. Outcome **unknown** → nothing to do by hand: the sweeper looks the key up after `APP_RECONCILE_AFTER_MINUTES`
   and marks it succeeded/failed. A new resolution for the dispute is refused (`side_effect_unsettled`) until then.
4. Permanent provider error (e.g. `card_account_closed`) → the dispute stays `UNDER_REVIEW`; the analyst decides.

## Tool calls failing

`ToolCallsMostlyFailing`: more than 20 % of calls to one tool failing for 5 minutes.
1. Provider status page / `kubectl -n $NS logs deploy/payments`.
2. If the provider is down: `APP_TOOLS_ENABLED=false` (Rollback, step 2). Approvals keep working; approved
   actions end `EXECUTION_FAILED` without calling the provider and can be re-run after recovery.
3. A schema change shows up as `tool_disabled` in the API logs (pins re-checked on every reconnect): review the
   provider's change, then `python scripts/pin_tool_schemas.py --write` in a PR.

## Tool calls denied

`ToolCallsDenied`: the gateway refused a tool repeatedly. `tool_calls.error` says why: tenant mismatch, approval
not bound to the call, pin mismatch, rate limit. Denials are the gateway working; repeated ones from the graph
are a bug — open an issue with the thread id.

## Graph node errors

`GraphNodeErrors`: one node raising repeatedly.
1. `GET /admin/threads/{id}/history` for an affected thread (from the logs: `node.error` with `thread_id`).
2. Transient (DB, model) → node retries already ran; after the cause is fixed, `POST /admin/threads/{id}/recover`.
3. Anything else is a bug: fix, deploy, then recover the failed threads.

## Abstention rate high

`AbstentionRateHigh`: over 35 % of runs escalate instead of deciding. Usual causes: policy documents missing for
a tenant (`ABSTAINED_INSUFFICIENT_EVIDENCE`), or a drafter regression (`ABSTAINED_FAILED_VERIFICATION`). Check the
outcome mix on the dashboard, then `python -m evals.run` against the current release.

## Resolution node slow

`ResolutionNodeSlow`: p95 of a node above 5 s. `retrieve_policy` → database load / index; `draft_resolution` →
model provider latency (LLM drafter); `execute` → tool provider latency.

## SLO: API availability

`SloBurn_api_availability_*`: /v2 5xx eating the 99.5 % budget. Page alerts mean the budget is gone in days.
1. Dashboard: 5xx by route. A spike right after a deploy → Rollback, step 3.
2. 504 `run_timeout` → model or tool latency (see Resolution node slow); 500 `run_aborted` → bug (recursion limit).
3. 429 is not a 5xx: admission control is working; check capacity (`docs/capacity.md`).

## SLO: start latency

`SloBurn_start_latency_*`: too many starts slower than 2.5 s. Check `graph_node_duration` by node; the DB pool
(`APP_DB_POOL_SIZE`, admission waits) and the model provider are the usual causes.

## SLO: execution success

`SloBurn_execution_success_*`: approved actions not executing. Same steps as Execution failures; if one tool is
the cause, Tool calls failing.

## Synthetic check failing

`SyntheticCheckFailing`: the in-cluster CronJob `synthetic` (`scripts/synthetic.py`) has had no successful run for 15 minutes.
`kubectl -n $NS logs job/<latest synthetic job>` shows which check failed (healthz, readyz or an authenticated
read) and how long it took. Readiness failing → database or checkpointer; authenticated read failing → JWT secret.

## Online eval drift

`OnlineEvalDrift`: a label-free check on production runs fell under its minimum (`evals/online_thresholds.yaml`).
1. `python -m evals.online score --hours 24` for the report.
2. For runs behind the drop: `python -m evals.online capture <thread_id> ...` → `evals/candidates.jsonl`. Candidates
   marked `DISAGREES` are failure classes: review the oracle label, add them to the next golden set with a test,
   fix, and record the incident in `docs/concerns.md`.

## Model cost spike

`ModelCostSpike`: one tenant's model spend is 3x the same hour yesterday (`model_cost_usd_total`). Check that
tenant's start rate (retries, a client loop) and the drafter's refine rate. Lower `APP_MODEL_REQUESTS_PER_SECOND`
or remove the tenant from `APP_RESOLUTION_TENANTS` while investigating. Daily report: `python scripts/cost_report.py`.

## Resolutions stuck

`ResolutionsStuck`: threads in `failed` or `resume_failed` for 15 minutes (`resolution_threads` gauge, refreshed
each sweep). `resume_failed` holds a stored decision: `POST /admin/threads/{id}/recover` replays it. `failed`:
see Graph node errors. A thread not worth finishing: `POST /admin/threads/{id}/cancel`.
