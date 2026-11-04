# Checkpointing and recovery (stage 10)

| Concern | Decision | Where |
|---|---|---|
| Saver per environment (T10.1) | `AsyncPostgresSaver` everywhere the service runs (dev, test, prod); `InMemorySaver` only in throwaway probes. Tests run against real Postgres so recovery tests exercise the real saver. | `bootstrap/lifespan.py` |
| Setup as a deploy step (T10.2) | `scripts/checkpoint_setup.py` runs with a DDL role before the new version starts (compose `migrate` service). The app refuses `APP_CHECKPOINT_AUTO_SETUP=true` outside dev/test. | `scripts/checkpoint_setup.py`, `compose.yaml`, settings validator |
| Restricted deserialisation (T10.3) | `LANGGRAPH_STRICT_MSGPACK=true` in the image and the test suite; state holds JSON-safe values only (ADR-0002), so nothing needs an allow-list. | `Dockerfile`, `tests/conftest.py` |
| Durability (T10.4) | Resolution runs use `durability="sync"`: a super-step's checkpoint is written before the next starts, so the step after a side effect never runs without the side effect being recorded. Assist keeps the default (`async`): a lost assist turn is re-asked, nothing external happened. | `orchestration/runners.py` |
| Thread registry (T10.5, T10.6) | `threads` table; ids are generated server-side as `resolution:<dispute_id>:<n>`; partial unique index allows one active thread per dispute and graph. | migration `0002`, `SqlThreadRegistry` |
| Tenant check on every access (T10.7) | The API never loads a thread by raw id: `ThreadRegistry.get(tenant_id, thread_id)` first (404 otherwise), and the dispute in the path must match. | `ResolutionRunner.view/resume/recover/history` |
| Side effects (T10.8) | Idempotency key fixed in `prepare_action` (before the pause) from tenant, dispute, thread, kind and amount. `execute`: reserve row (`ON CONFLICT DO NOTHING`) → call with the key → finish. Replay after success makes no call. | `domain/approval_policy.py`, `SqlSideEffectLedger`, `execute` |
| Outbox (T10.9) | `notify` writes one `outbox` row per thread (`dedupe_key = <thread>:completed`). Relay + consumers are out of scope (deferred DF3). | `SqlOutbox` |
| Schema upgrade (T10.10) | Assist: `upgrade_state` stamps `schema_version = 2` on v1 threads; legacy keys stay declared. Resolution starts at schema 1. | `orchestration/assist.py`, `docs/state_changelog.md` |
| Retention (T10.11) | `scripts/prune_checkpoints.py` deletes checkpoints of `done/failed/cancelled` threads older than `APP_CHECKPOINT_RETENTION_DAYS` (90). The business record (threads row, approvals, side effects, audit, dispute timeline) is kept. Run daily. | script + test |
| Encryption (T10.12) | In transit: settings refuse a staging/prod `APP_DATABASE_URL` without `sslmode=require`/`verify-full`. At rest: managed Postgres storage encryption; checkpoint payloads hold no card numbers or secrets (SR6). App-level `EncryptedSerializer` (LANGGRAPH_AES_KEY) is deferred until state must hold regulated data. | settings validator |
| Admin inspection (T10.13) | `GET /admin/threads/{id}/history` (admin, tenant-scoped, ≤ 50): step, nodes run and keys written, next, outcome — no values. Audited. | `api/routers/admin.py` |

## Recovery procedure

| Situation | Signal | Action |
|---|---|---|
| Node raised `TransientError` | retried 3× automatically | nothing, unless all attempts fail → thread `failed` |
| Run failed mid-way (bug, crash) | thread `failed`; error log | fix → `POST /admin/threads/{id}/recover` (`ResolutionRunner.recover`). Completed steps are not re-run; the failed step re-runs idempotently. Proven by `test_crash_between_refund_and_dispute_update_recovers_without_repeating_the_refund`. |
| Crash after the tool call, before recording it | side effect `pending` | RetryPolicy / recover re-runs `execute` with the same key; the payments side dedupes → one refund (`test_transient_failure_after_the_tool_call_retries_without_a_second_refund`) |
| Tool outcome unknown after gateway retries | `side_effects.status = unknown`, outcome EXECUTION_FAILED with the key | the sweeper looks the key up (`payments.get_refund` / `network.get_submission`) after `APP_RECONCILE_AFTER_MINUTES`: found → `succeeded` + outbox `dispute.side_effect_reconciled` for ops; not found → `failed` |
| Recursion limit | 500 `run_aborted`, `alert=true` log | treat as a bug; inspect history endpoint |
| Decision stored, resume failed | thread `resume_failed` | `POST /admin/threads/{id}/recover` replays the stored decision |
| Thread stuck and not worth finishing | thread `failed`/`resume_failed` | `POST /admin/threads/{id}/cancel` frees the dispute for a new run |

## Compatibility evidence (live v1.4.0 thread)

The dev database holds a thread written by v1.4.0 this morning (5 checkpoints, no `schema_version`). After
migrating to `0002` and starting this build:

```text
before: schema_version None history 1 keys ['citations', 'dispute', 'evidence_kinds', 'history', 'policy_hits', 'question', 'reason_family', 'recommendation', 'suggested_resolution', 'tenant_id', 'transaction']
response 200 … 'suggested_resolution': 'ACCEPT', 'turns': 2 … Deprecation: @1793491200
after: schema_version 2 history 2 evidence items 11
```

That thread was then exported as `tests/fixtures/checkpoints/v2_assist.json`; `tests/graph/test_checkpoint_compat.py`
resumes both the v1 and v2 fixtures on every run.
