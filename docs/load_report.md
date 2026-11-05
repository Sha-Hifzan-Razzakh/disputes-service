# Load report (T16.11, T16.12)

Environment: one container, **2 vCPU** shared by the load generator, the app (uvicorn), both MCP servers and
Postgres 16; rules drafter; tools enabled over streamable HTTP. Each request starts a resolution on a fresh dispute
(VISA 10.4 with 3-D Secure → CONTEST → pauses for approval; ≈ 14 checkpoint writes with `durability="sync"`).
Script: `scripts/load_ramp.py` (32 requests per step).

## Default settings (1 worker, DB pool 5 + 5, checkpoint pool 4, 8 run slots, 2 s admission wait)

|---|---|---|---|---|---|---|
| 1 | 32 | {202: 32} | 147.5 | 177.5 | 177.7 | 6.7 |
| 4 | 32 | {202: 32} | 611.3 | 715.8 | 716.4 | 6.4 |
| 8 | 32 | {202: 32} | 1084.3 | 1335.8 | 1348.6 | 7.0 |
| 16 | 32 | {202: 32} | 2010.7 | 2308.3 | 2353.7 | 7.5 |
| 32 | 32 | {202: 16, 429: 16} | 1818.4 | 2533.7 | 2532.4 | 12.6 |

One process saturates at ≈ 7 starts/s; above that, latency grows by queueing (linear in concurrency) and admission
control turns the excess into fast 429s (16 of 32 at concurrency 32) instead of 504s. Single-request p95 is
178 ms — well inside the 3 s spec target.

## Squeeze: DB pool 1 + 5, checkpoint pool 1, **16** run slots (invalid configuration)

| concurrency | requests | status codes | p50 ms | p95 ms | max ms | throughput rps |
|---|---|---|---|---|---|---|
| 8 | 32 | {504: 32} | None | None | None | 0.3 |
| 16 | 32 | {504: 32} | None | None | None | 0.5 |

**Failure found:** every run timed out. Each running thread holds one pooled connection for its advisory lock
and needs more for its own reads/writes; with more run slots than pool capacity, runs wait on each other until
the 30 s API timeout cancels them (logs: `node.error … CancelledError`, no business errors). **Fix:** settings
now refuse `APP_MAX_CONCURRENT_RUNS > APP_DB_POOL_SIZE + APP_DB_MAX_OVERFLOW − 2`
(`tests/unit/test_settings.py`).

## Squeeze, valid: DB pool 1 + 5, checkpoint pool 1, 4 run slots

| concurrency | requests | status codes | p50 ms | p95 ms | max ms | throughput rps |
|---|---|---|---|---|---|---|
| 4 | 32 | {202: 32} | 593.7 | 747.8 | 764.6 | 6.5 |
| 8 | 32 | {202: 32} | 1423.3 | 1833.3 | 1845.5 | 5.4 |
| 16 | 32 | {202: 28, 429: 4} | 2116.2 | 2509.6 | 2528.6 | 6.6 |

Degrades gracefully: same throughput band, higher latency, a few 429s at 16; no timeouts.

## Scale-out: 2 workers (before the session-scope fix)

| concurrency | requests | status codes | p50 ms | p95 ms | max ms | throughput rps |
|---|---|---|---|---|---|---|
| 8 | 32 | {202: 30, 409: 2} | 632.0 | 1533.3 | 1535.7 | 9.6 |
| 16 | 32 | {202: 32} | 1606.3 | 2784.7 | 2788.2 | 8.9 |
| 32 | 32 | {202: 30, 429: 2} | 1641.7 | 2766.9 | 2769.0 | 11.5 |

**Failure found:** 409s. The DB session dependency committed *after* the response was sent (FastAPI default
`scope="request"`), so the load script's next PATCH — served by the other worker — read the previous version and
got `version_conflict`; the dispute then stayed OPENED and the start was refused. A v1 bug that one worker hid.
**Fix:** `Depends(db_session, scope="function")` commits before responding (guard test in
`tests/unit/test_schemas.py`).

## Scale-out: 2 workers (after the fix)

| concurrency | requests | status codes | p50 ms | p95 ms | max ms | throughput rps |
|---|---|---|---|---|---|---|
| 8 | 32 | {202: 32} | 1022.2 | 1401.4 | 1408.2 | 8.6 |
| 16 | 32 | {202: 32} | 1516.4 | 1775.7 | 1807.1 | 9.9 |
| 32 | 32 | {202: 30, 429: 2} | 1843.2 | 3135.8 | 3143.7 | 10.2 |

No errors; throughput ≈ 10/s (+40 %). Scaling is sub-linear here because two cores also run Postgres, both MCP
servers and the load generator. The per-thread advisory lock is cross-process, so workers and replicas can be
added without coordination.
