---
name: incident-to-concern
description: Use after any disputes-service incident, failed deploy, review finding or near miss, to record it in the concern table with a new control and a proof, so the same failure class cannot come back unnoticed.
---

# Incident → concern → control

1. Write the question the incident answered as "what happens if …?" (one sentence, no blame).
2. Find the row in `ops/concerns.py` that owns that question; if none, add the next `Cnn`.
3. Name the control that now answers it (`mechanism`, `where`) — a code change, a gate in CI, an alert with a
   runbook section, or an explicitly accepted risk (`status="accepted"` with the reason).
4. Add at least one proof that fails without the control: a test (`module::function`), an eval case
   (`evals:<id>`, via the add-golden-case skill), a CI job (`ci:<workflow>/<job>`) or a drill script.
5. Add the incident to `incidents` (date or PR, one line).
6. `uv run python ops/concerns.py` and `uv run pytest tests/unit/test_concerns.py`.
7. If an alert would have caught it sooner, add one with a `runbook_url` and a runbook section.
