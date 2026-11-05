## What and why
<!-- one paragraph; link the issue/spec -->

## Graph change
<!-- before/after diagrams from `uv run python scripts/draw_graphs.py`, or "none" -->

## State change
| Field | Change | Reducer | Checkpoint impact |
|---|---|---|---|
<!-- or "none" -->

## Traceability
| Criterion | Code path | Test | Result |
|---|---|---|---|

## Evidence (at the head commit)
- [ ] `pytest` + coverage · [ ] chaos (if graph/runtime changed) · [ ] evals vs baseline · [ ] trace/log sample

## Rollout and rollback
Flags and defaults · migrations in order · rollback steps someone else can run.

## Known limits

## Change type
Tick what this PR changes; each brings its checks (docs/maintenance.md).
- [ ] **State schema** (fields, reducers) → state changelog, `upgrade_state`, release checkpoint resumes
- [ ] **Graph topology** → `scripts/draw_graphs.py`, `test_compiled_topology_matches_design`, scenario map
- [ ] **Migration** → additive only, `test_migrations` drift check, deploy notes order
- [ ] **Tool / provider** → skill `new-tool`: policy, pin, gateway tests, reconciliation lookup
- [ ] **Prompt / model / drafter** → snapshot, golden evals, `run-llm-evals` label
- [ ] **Dependency upgrade** → upgrade notes, compat + chaos (`run-chaos` label)
- [ ] **Deploy / infra** → deploy rehearsal on this PR (staging job), `iac` job, `docs/environments.md`
- [ ] **Alert / SLO** → runbook section, `ops/slo/slos.py` regenerated
- [ ] **Incident fix** → skill `incident-to-concern`

## Review checklist
- [ ] Node = one responsibility · [ ] One owner/reducer per field · [ ] Routes total, loops bounded
- [ ] No clients in nodes · [ ] Timeouts + retry level · [ ] Tool authZ from identity
- [ ] Thread ownership check · [ ] No lost parallel updates · [ ] Idempotent side effects
- [ ] Checkpoint compat note · [ ] Logs masked · [ ] Tests + evals · [ ] Docs/diagram updated
