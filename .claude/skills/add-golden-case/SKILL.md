---
name: add-golden-case
description: Use when a production run, an incident or a review finding should become a permanent evaluation case for the disputes-service resolution workflow (golden set, oracle label, eval baseline).
---

# Add a golden case

The golden set is the release gate (`evals/thresholds.yaml`). Cases come from three places: hand-written spec
cases, the generated grid, and production failures captured with `evals.online capture`.

1. Get the case:
   - production: `uv run python -m evals.online capture <thread_id>` appends an anonymised, oracle-labelled
     candidate to `evals/candidates.jsonl`, with what production did and whether it `DISAGREES` with the oracle;
   - review/incident: write the case by hand in the golden format (`evals/README.md`).
2. Decide the label. If the oracle (`evals/oracle.py`, spec §4) is right, production was wrong: keep the label.
   If the oracle is wrong, the spec changed: update `docs/spec.md` and the oracle in the same PR.
3. Add it to `evals/golden_v0.jsonl` with the next free `Gnn` id and a `note` saying where it came from; remove it
   from `candidates.jsonl`.
4. `uv run python evals/generate_v1.py` (asserts every hand label equals the oracle), then
   `uv run python -m evals.run`. A metric change you intended → `--write-baseline <version>`; otherwise fix the code.
5. If the case pins a behaviour a unit/graph test can reach faster, add that test too and cite both in
   `ops/concerns.py`.
