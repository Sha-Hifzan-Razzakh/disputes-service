# Maintenance (stage 26)

| Cadence | What | How | Output |
|---|---|---|---|
| Every PR | change-type checks | PR template "Change type" section | ticked checks in the PR |
| Every PR | deprecations | `filterwarnings = error::DeprecationWarning …` in `pyproject.toml` | red CI before a removal bites |
| Every PR | schema drift | `tests/integration/test_migrations.py` | models and migrations agree |
| Every release | release checkpoints | skill `release-checkpoint` | `tests/fixtures/checkpoints/release_<v>_*.json` resuming in CI |
| Weekly | dependency PRs | Dependabot (`.github/dependabot.yml`), grouped LangChain and OTel | PRs with green CI + evals |
| Monthly | refresh | `.github/workflows/monthly.yml` | issue "Monthly refresh YYYY-MM" |
| Quarterly | ADR and dead-code review | below | cleanup PRs, ADR updates |

## Supported checkpoint versions

Every committed release fixture is supported: a thread paused by that release resumes in current code
(`test_paused_resolution_from_each_release_resumes_to_completion`). Dropping one is a breaking change: it needs
an ADR, a CHANGELOG entry and evidence (`threads.graph_version`) that no open thread still carries it:
`SELECT graph_version, count(*) FROM threads WHERE status IN ('running','awaiting_approval','resume_failed') GROUP BY 1`.

## Upgrades (T26.5)

For every dependency PR (Dependabot or the monthly report):
1. Read the changelog between the two versions; note breaking changes and deprecations in the PR body
   ("Upgrade notes": what changed, what we use of it, what we checked).
2. LangGraph / checkpoint packages: run the checkpoint compat tests and the chaos suite (`run-chaos` label).
3. MCP SDK: re-pin tool schemas (`scripts/pin_tool_schemas.py`) and run the HTTP smoke.
4. Model or prompt changes: `run-llm-evals` label; never merge below the thresholds.
5. Merge only with green CI, green evals and the notes filled in.

## Quarterly review (T26.7)

1. `PROM_URL=… python scripts/node_usage.py --days 90`: nodes that never ran. Remove them, or add the golden or
   synthetic case that proves the path still works.
2. State fields: every field in `ResolutionState` still has a writer and a reader (`OWNERS` in `state.py`);
   retire unused ones through `upgrade_state`, never by deleting a key old checkpoints carry.
3. ADRs (`docs/adr/`): still true? Supersede, don't edit history.
4. `ops/concerns.py`: every incident of the quarter has a row (skill `incident-to-concern`); accepted risks are
   still acceptable.
5. Thresholds: `evals/thresholds.yaml` and `evals/online_thresholds.yaml` against the last quarter's numbers.

## Team skills (T26.8)

`.claude/skills/`: `release-checkpoint`, `add-golden-case`, `new-tool`, `incident-to-concern`. Used in PR #14:
release-checkpoint (2.1.0 fixtures) and incident-to-concern (C16 MCP host names, C21 image CVE, C27 migrate
import). add-golden-case and new-tool have no use yet: record the first PR that uses each here.
