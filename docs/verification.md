# Verification (stages 14–17)

What proves the resolution workflow works, and where. One row per workbook task. "Deviation" means the task's
suggested artifact was replaced, and the row says by what and why.

| Gate | Command | Runs | Result at M4 |
|---|---|---|---|
| Unit + graph + API + integration | `uv run pytest --cov` | every PR | 263 passed, coverage 93 % (gate 90 %) |
| Chaos | `uv run pytest -m chaos` | nightly (`.github/workflows/nightly.yml`) | 13 passed |
| Evals | `uv run python -m evals.run` | every PR (`ci.yml` job `evals`) | PASS, 189 cases; `docs/eval_report_2.0.0-rules.md` |
| LLM evals | promptfoo, Ragas, LangSmith sync | nightly, each skips without its key | not run yet: no model key in this environment |
| Load | `scripts/load_ramp.py` | by hand before a release | `docs/load_report.md`, `docs/capacity.md` |

## Stage 14 — unit

| Task | Where | Notes |
|---|---|---|
| T14.1 runner config | `pyproject.toml` `[tool.pytest.ini_options]` | `asyncio_mode=auto`, `--strict-markers`, markers `chaos`/`eval`; default run excludes both |
| T14.2 fakes | `tests/fakes.py` | one in-memory fake per port + `FakeToolGateway` (transient, permanent, malformed, delay knobs), `make_ctx`/`make_runtime` |
| T14.3 node per contract row | `tests/unit/nodes/test_resolution_nodes.py` | 16 tests: each node called directly with a fake runtime |
| T14.4 owned keys | `tests/unit/test_state_ownership.py`, `tests/graph/test_contracts.py::test_nodes_write_only_owned_keys_and_output_is_out_schema` | static table + every node's actual writes during real runs |
| T14.5 routers | `tests/unit/test_routing.py` | every router, every branch, parametrized |
| T14.6 reducers | `tests/unit/test_reducers.py` | Hypothesis properties for `merge_evidence` and `write_once` |
| T14.7 schema validation | `tests/unit/test_schemas.py` | request models, resume payloads; also `test_db_session_commits_before_the_response_is_sent` (bug found by load testing) |
| T14.8 malformed structured output | `tests/unit/test_structured.py` | non-JSON, wrong enum, extra fields → `INVALID` draft that `verify` rejects; timeouts/429 → `TransientError` |
| T14.9 adapters vs timeout/429/500 | `tests/unit/test_adapters.py` | MCP gateway: timeout, rate limited, server crash (retryable), malformed (side-effect → unknown) |
| T14.10 prompt snapshot | `tests/unit/__snapshots__/resolution_prompt.txt` | also asserts the analyst note is fenced and cannot close its fence |
| T14.11 coverage threshold | `[tool.coverage.report] fail_under = 90`, CI `--cov` | 93 % branch-aware |
| T14.12 tests before a turn ends | `.claude/settings.json` Stop hook | runs `tests/unit -x`, exit 2 blocks the stop on failure |

## Stage 15 — graph and integration

| Task | Where | Notes |
|---|---|---|
| T15.1 topology | `tests/graph/test_contracts.py::test_compiled_topology_matches_design` | compiled nodes and edges vs the design's list |
| T15.2 `run_and_trace` | `tests/graph/helpers.py` | node path per run, used by scenario tests |
| T15.3 Gherkin → path tests | `tests/graph/test_scenarios.py` | `SCENARIOS` maps all 25 scenarios in `specs/*.feature` to a test (or the golden case for G22); a test fails if a scenario is unmapped or its test is missing |
| T15.4 approve / reject | `tests/graph/test_resolution_graph.py` (`o1`, `o3`, `o7`, `o8`, four-eyes) | through the API and the real interrupt |
| T15.5 real infrastructure | **Deviation:** no testcontainers. Tests use a real Postgres 16 (local, CI `services: postgres`); each session creates and drops `disputes_test`. Same engine, one fewer moving part in CI. MCP servers run in memory and, once, over real HTTP (below). |
| T15.6 Postgres checkpointer | all graph/API tests use `AsyncPostgresSaver`; `tests/graph/test_checkpoint_compat.py` resumes v1.4.0 checkpoint fixtures |
| T15.7 MCP end to end | `tests/integration/test_mcp_servers.py`, `test_tool_gateway.py`, `test_api_v2.py::test_mcp_server_over_real_http_with_token` | the last one starts the payments server with uvicorn and calls it over streamable HTTP with a bearer token |
| T15.8 API end to end | `tests/integration/test_api_v2.py` | 504 then admin recover → awaiting approval; partial refund 37.50; 429 admission |
| T15.9 tenant isolation | `tests/integration/test_isolation.py` | 8-route cross-tenant 404 matrix, retrieval scoped by `policy_chunks.tenant_id`, gateway refuses a foreign tenant |
| T15.10 real-model cassettes | **Deferred:** needs an API key. The LLM drafter is covered offline (T14.8, T14.10) and by the nightly promptfoo suite once `ANTHROPIC_API_KEY` is set. |
| T15.11 Studio sign-off | **Replaced:** `GET /admin/threads/{id}/history` + `scripts/draw_graphs.py` give the same step-by-step view without a desktop tool; every scenario is stepped by T15.3. |

## Stage 16 — chaos and load

| Task | Where | What it proves |
|---|---|---|
| T16.1 suite | `tests/chaos/` (auto-marked `chaos`) | nightly job |
| T16.2 model timeout/429 | `test_model_failures.py` | two transient failures then success; a model that stays down fails the run cleanly and it recovers |
| T16.3 vector store down | `test_vectorstore_down.py` | brief outage absorbed by `retrieve_policy`'s retry; long outage fails the run, which recovers from the last checkpoint |
| T16.4 DB reset/latency | `test_db_faults.py` | connection reset inside the approval write is retried with one row created; added latency is tolerated |
| T16.5 malformed tool output | `test_tool_malformed.py` | refund recorded but reply malformed → `unknown`, reconciled by key, one refund |
| T16.6 invalid state | `test_invalid_state.py` | a second terminal outcome is refused; forged resume values are refused |
| T16.7 checkpointer write failure | `test_checkpointer_failure.py` | checkpoint after `execute` lost; recovery re-runs `execute`, finds the side effect and makes no second refund |
| T16.8 crash and resume | `test_crash_resume.py` | worker cancelled inside `execute` after the refund; recovery re-sends the same key and the provider dedupes |
| T16.9 50-way fan-out | `test_fanout.py` | 50 `Send`s, peak concurrency ≤ 4, deterministic merge, cap of 12 queries |
| T16.10 concurrent resumes | `test_concurrent_resume.py` | one resume wins, the other gets 409 `thread_busy`; one side effect. Found the missing per-thread lock (fixed: advisory lock) |
| T16.11 load ramp | `scripts/load_ramp.py`, `docs/load_report.md` | ~7 starts/s per worker, p95 178 ms single client |
| T16.12 squeeze | `docs/load_report.md` | found: run slots > pool → every run 504 (fixed: settings validator); 2 workers → false 409 (fixed: commit before response) |
| T16.13 admission control | `api/deps.py::bounded_run` | 429 `too_many_runs` + `Retry-After` after `APP_ADMISSION_WAIT_S` |
| T16.14 measured limits | `docs/capacity.md` | |

## Stage 17 — evals

| Task | Where | Notes |
|---|---|---|
| T17.1 golden set | `evals/golden_v1.jsonl` (189) | 32 hand cases (v0) + a generated grid labelled by `evals/oracle.py`, which imports nothing from `app/`; the generator asserts the oracle agrees with every hand label |
| T17.2 path labels | `expected.path`, `expected.after_approval.path` | per case |
| T17.3 LangSmith dataset | `evals/langsmith_sync.py` | **Key-gated:** idempotent mirror; the JSONL in git stays the source of truth |
| T17.4–T17.8, T17.10 scorers | `evals/scorers/{retrieval,generation,abstain,trajectory,tools,ops}.py` | |
| T17.9 prompts + red team | `evals/promptfoo/` | **Key-gated.** Production prompt, 33 cases including 8 injection probes; the assertion reuses the production output schema. Checked offline by `tests/unit/test_promptfoo_suite.py`. Ragas faithfulness: `evals/ragas_faithfulness.py` |
| T17.11 runner | `evals/run.py` | own database `disputes_eval`, real graph, approvals and MCP servers |
| T17.12 baseline | `evals/baselines/2.0.0-rules.json` | |
| T17.13 thresholds | `evals/thresholds.yaml` | per-metric min/max + 0.02 regression tolerance against the newest baseline |

### Found by the evals

Approvals for disputes with ≤ 24 h to the deadline were created already expired: TTL = `respond_by − 24 h` was in
the past. Spec D7 amended (decision window, 15 min execution buffer), fixed in `domain/approval_policy.py`, unit
test `test_close_to_the_deadline_the_approver_still_gets_time`.

### Known gaps

- DB spans: `opentelemetry-instrumentation-sqlalchemy` does not support SQLAlchemy 2.1, so adapters open a
  `db.transaction` span themselves; per-statement spans come back when the instrumentor supports 2.1.
- No model-backed number exists yet. The rules drafter scores 1.0 by construction on the cases it encodes; the
  quality claim for the LLM drafter waits for the first nightly run with a key.
