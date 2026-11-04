# Capacity and limits (T16.13, T16.14)

Measured on 2 vCPU (docs/load_report.md); treat as a lower bound per worker on dedicated cores.

| Quantity | Measured / chosen | Notes |
|---|---|---|
| Starts per worker | ≈ 7/s (rules drafter) | CPU-bound in-process: graph super-steps, 14 sync checkpoint writes, JSON logging, spans |
| Single start latency | p50 148 ms, p95 178 ms | spec target ≤ 3 s |
| Checkpoint writes per start | 14 (parent + evidence subgraph) | ≈ 15–20 KB per paused thread |
| Run slots per worker | `APP_MAX_CONCURRENT_RUNS` = 8 | admission control: wait ≤ `APP_ADMISSION_WAIT_S` (2 s) then 429 + `Retry-After: 2` |
| DB connections per running thread | 1 held (advisory lock) + 1–2 short | settings refuse slots > pool + overflow − 2 |
| DB pool per worker | 5 + 5 overflow | Postgres connections ≈ workers × (10 + checkpoint pool 4) — keep under `max_connections` |
| LLM drafter (when enabled) | client limiter 2 rps, burst 5 | becomes the bottleneck before CPU: ~2 starts/s per worker unless the limit is raised |
| Tool calls | 5/s per tenant per tool, burst 10 | token bucket in the gateway |

## Sizing rule

workers = ceil(peak starts per second ÷ 7) on dedicated cores, plus one for headroom; approvals and reads are cheap
next to starts. Keep `max_concurrent_runs × workers × 3 < Postgres max_connections`.

## Operational guidance

- A run cut off by the API timeout keeps status `running` (it stays recoverable). Watch for threads `running`
  longer than 2 × `APP_RUN_TIMEOUT_S` and finish them with `POST /admin/threads/{id}/recover` or free the dispute
  with `/cancel`. Automatic stale detection is deliberately not done: a stale thread may already hold an approved,
  keyed side effect, and only a human should decide whether a second thread may start.
- 429 `too_many_runs` with `Retry-After` is the expected overload signal; clients back off and retry.
