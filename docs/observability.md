# Observability (stage 13)

| Signal | What | Where |
|---|---|---|
| Request id (T13.1) | `X-Request-ID` in/out, bound to every log line; error envelope carries it | `app/api/middleware.py` |
| Run names and tags (T13.2) | `run_name` = graph, tags `resolution`/`assist` + `tenant:<id>`, metadata tenant/user/request | `orchestration/runners.py::run_config` |
| Traces (T13.3, T13.4) | OpenTelemetry: HTTP server spans (FastAPI), DB spans (SQLAlchemy), one span per graph node (`resolution.<node>` with `thread_id`), one per tool call (`tool <name>`). Exporter `APP_OTEL_EXPORTER=none|console|otlp` (OTLP/HTTP to any collector). | `bootstrap/observability.py`, `orchestration/instrument.py`, tool gateway |
| Logs (T13.5) | JSON: `ts`, `level`, `event`, `request_id`, `thread_id`, `tenant_id`, `trace_id`, `span_id`; `node.end` (ms, keys written, goto), `node.error`, `http`, `tool_disabled`, `sweeper.*` | `bootstrap/observability.py` |
| Metrics (T13.6) | `/metrics` (Prometheus): `graph_node_duration_milliseconds`, `graph_node_errors_total`, `resolution_outcomes_total{outcome}`, `tool_calls_total{tool,status}`, `tool_call_duration_milliseconds`, `http_server_duration_milliseconds` | OpenTelemetry → `PrometheusMetricReader` |
| Masking (T13.7) | keys in `state.SENSITIVE` + note/rationale/tokens → `[REDACTED]`; card-number and e-mail patterns → `[PAN]` / `[EMAIL]` anywhere in values; tool arguments stored only as sha256 | `redact` processor, `tool_calls.args_sha256` |
| Dashboard (T13.8) | `ops/dashboards/resolution.json` (Grafana): outcomes, abstention rate, node p95, node errors, tool calls/latency, execution failures, expiries, /v2 5xx ratio | |
| Alerts (T13.9) | `ops/alerts/disputes.rules.yml`: execution failures (page), tool failure ratio (page), repeated denials, node errors, abstention rate, slow nodes — each with a runbook pointer | |

`tests/api/test_ops_artifacts.py` fails if a dashboard or alert references a series the service does not export.

## Diagnosing a failed run in under two minutes (stage 13 exit)

1. Alert or user report gives a request id or dispute id.
2. Logs: `request_id=<id>` → `node.error` line with `thread_id`, `node`, `error`, `trace_id`.
3. Trace `<trace_id>`: HTTP span → node spans → the failing node span (status ERROR) → its DB or tool span.
4. `GET /admin/threads/<thread_id>/history`: which nodes ran, which keys they wrote, where it stopped.
5. Fix or retry: `POST /admin/threads/<thread_id>/recover` (idempotent).

Proven by `tests/api/test_observability.py::test_failed_run_is_traceable_from_spans_and_logs`.
