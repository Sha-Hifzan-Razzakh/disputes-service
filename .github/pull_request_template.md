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

## Review checklist
- [ ] Node = one responsibility · [ ] One owner/reducer per field · [ ] Routes total, loops bounded
- [ ] No clients in nodes · [ ] Timeouts + retry level · [ ] Tool authZ from identity
- [ ] Thread ownership check · [ ] No lost parallel updates · [ ] Idempotent side effects
- [ ] Checkpoint compat note · [ ] Logs masked · [ ] Tests + evals · [ ] Docs/diagram updated
