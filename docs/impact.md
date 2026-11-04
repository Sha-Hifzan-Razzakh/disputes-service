# Impact analysis (T03.5, T03.8, T03.9)

## Live threads in the checkpoint store (T03.5)

Query against the dev database (read-only), main@2462be0:

```sql
SELECT checkpoint_ns, split_part(thread_id, ':', 1) AS namespace,
       count(DISTINCT thread_id) AS threads, count(*) AS checkpoints,
       min((checkpoint->>'ts')::timestamptz) AS oldest, max((checkpoint->>'ts')::timestamptz) AS newest
FROM checkpoints GROUP BY 1, 2;
```

| checkpoint_ns | namespace | threads | checkpoints | oldest | newest |
|---|---|---|---|---|---|
| `''` | `assist` | 1 | 5 | 2026-11-04 09:33:59Z | 2026-11-04 09:33:59Z |

Checkpoint payload 5.3 KB, blobs 3.0 KB. The dev store holds one thread (the one exported to
`tests/fixtures/checkpoints/v1_assist.json`); production counts are unknown from here, so the decision
assumes every dispute that was ever assisted has a live thread.

**Compatibility decision: support.** v1 `assist:<dispute_id>` threads must keep resuming and keep their
`history` (spec M8). The v2 assist graph keeps the same thread ids, keeps `history` with the same reducer,
tolerates the legacy `tenant_id` and `policy_hits` keys, and upgrades `schema_version` (missing → 2) in its
first node. Resolution threads use a new namespace (`resolution:`), so they never meet v1 state.
Proof: `tests/graph/test_checkpoint_compat.py` loads the committed fixture into a fresh checkpointer and
asks a new question (stage 10/16).

## Invariants that must stay true (T03.8)

Recovered from the v1 code and tests; each keeps its existing test.

| # | Invariant | Where enforced in v1 | Intent | Kept by |
|---|---|---|---|---|
| V1 | Dispute amount > 0 and ≤ transaction amount | `Dispute.open` | Network rule: cannot dispute more than captured | unchanged |
| V2 | Transitions only along `TRANSITIONS`; resolve only from UNDER_REVIEW | `Dispute.apply` | Lifecycle integrity | v2 `apply_resolution` calls `submit_for_review` first when the dispute is EVIDENCE_GATHERING |
| V3 | No resolve after `respond_by` | `Dispute.apply` | Network deadline | also checked in `load_case` (D1) and verify (R3) |
| V4 | Every write bumps `version`; stale `expected_version` → 409 | `SqlDisputeRepository.save` | Lost-update protection | v2 apply uses the same path |
| V5 | Every read is tenant-filtered; other tenant → 404, not 403 | repositories, `test_tenant_isolation` | Don't leak existence | v2 repositories and `/v2` routes follow it |
| V6 | Network policy docs (`tenant_id IS NULL`) are shared; merchant docs are not | `SqlPolicyStore.search` | Shared rule summaries | unchanged; evidence ids carry scope |
| V7 | Money is Decimal, 2 dp, half-even, one currency per dispute | `Money` | No float money | v2 partial amounts use `Money` |
| V8 | One assist thread per dispute; history accumulates | `thread_id = assist:<id>`, `operator.add` | Conversation memory | compat decision above |
| V9 | Error envelope `{"error": {code, message, request_id}}` | `app/api/errors.py` | Client contract | new codes added, shape unchanged |

## Impact list (T03.9)

Every v1 element the change touches, who reads it, and what changes. No row has an unknown reader.

| Element | Readers today | Change | Risk | Test that guards it |
|---|---|---|---|---|
| `app/orchestration/assist.py` nodes | compiled graph | Take `runtime: Runtime[Ctx]`; drop `get_container()`; later (M2) reuse the evidence subgraph | v1 answers change | `tests/graph/test_assist_graph.py` unchanged and green |
| `AssistState` | v1 checkpoints, `AssistService` | v2: add `schema_version`, `evidence*`, `queries`, `retrieval_attempts`; keep legacy keys | old threads fail to load | compat test with fixture |
| `bootstrap.container.get_container/set_container` | nodes only | Removed; container lives on `app.state` | hidden callers | `lint-imports`, full test run |
| `application/assist.py` `AssistService` | `api.deps`, `api.routers.assist` | Moves to `orchestration/runners.py` as `AssistRunner`; case loading moves to `application/case.py` `CaseService` | import errors | API tests |
| `api/deps.py` repository construction | all routers | Container exposes `dispute_service(session)`, `case_service(session)`, `ledger(session)` | wiring mistakes | API tests |
| `api/routers/reference.py` `SqlLedger` | `/v1/transactions/{ref}` | Uses container factory | same | `test_transaction_lookup_scoped` |
| `bootstrap/lifespan.py` `saver.setup()` per boot | startup | M1: gated by `APP_CHECKPOINT_AUTO_SETUP` (default true in dev); M2: deploy-step script | prod app needs DDL rights | lifespan test with flag off |
| `bootstrap/settings.py` | container, deps | Add run limits, model limits, evidence thresholds, approval TTL, MCP URLs | env var drift | `.env.example` + settings test |
| `api/errors.py` | all routes | Add `RunTimeout` → 504, `ApprovalDecided` → 409, `ApprovalExpired` → 410 | envelope drift | API tests |
| `/v1/disputes/{id}/assist` | analysts | Response gains `Deprecation` and `Sunset` headers in M2; body unchanged | clients that reject unknown headers | contract test |
| `domain/dispute.py` | services, repos | No new transition needed: REQUEST_INFO uses existing `start_evidence` (UNDER_REVIEW → EVIDENCE_GATHERING) | — | domain tests |
| `migrations/` | deploy | New `0002` (M2/M3): `threads`, `approvals`, `side_effects`, `tool_calls`, `outbox`, `audit_log`, `tenant_dispute_policy` | lock time on deploy | migration up/down test |
| `seeds/` | tests, dev | Add tenant dispute policy rows; eval tenant `evalco` | — | seed idempotency |
| CI workflow | PRs | Add `lint-imports`; later eval job | — | CI run |

New surface (no v1 readers): `/v2/disputes/{id}/resolutions` (POST, GET, `…/stream`), `/v2/approvals` (GET),
`/v2/approvals/{id}/decision` (POST), `/admin/threads/{id}/history` (GET), `/admin/threads/{id}/resume` (POST),
`/metrics`; MCP servers `payments` and `network-gateway`.
