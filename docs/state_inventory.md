# State inventory (T03.3) and ownership mirror (T05.3)

## v1 `AssistState` as found (`app/orchestration/assist.py`, main@2462be0)

Writers are keys returned by nodes or passed as graph input; readers are `state[...]` / `state.get(...)` and
`out[...]` after invoke. All fields are persisted (Postgres checkpointer). Found with:

```bash
rg -n '"<field>"\s*:' app/orchestration app/application/assist.py                       # writers
rg -n 'state\["<field>"\]|state\.get\("<field>"|out\["<field>"\]|out\.get\("<field>"' app  # readers
```

| Field | Writers | Readers | Reducer | Flag |
|---|---|---|---|---|
| `tenant_id` | input (`AssistService.ask`) | `retrieve_policy` | replace | identity in state → move to `Ctx` (T05.5) |
| `dispute` | input | `classify_reason`, `retrieve_policy`, `draft_recommendation` | replace | — |
| `transaction` | input | `draft_recommendation` | replace | sensitive (AVS/CVV results) |
| `evidence_kinds` | input | `draft_recommendation` | replace | — |
| `question` | input | `retrieve_policy`, `draft_recommendation` | replace | sensitive (free text) |
| `reason_family` | `classify_reason` | `retrieve_policy`, `draft_recommendation` | replace | — |
| `policy_hits` | `retrieve_policy` | `draft_recommendation` | replace | full chunk text in checkpoint (size, confidentiality) |
| `recommendation` | `draft_recommendation` | API (`out[...]`) | replace | sensitive (free text) |
| `suggested_resolution` | `draft_recommendation` | API | replace | — |
| `citations` | `draft_recommendation` | API | replace | — |
| `history` | `draft_recommendation` | API (`len`) | `operator.add` | grows unbounded; must keep accumulating after upgrade |

Multi-writer fields: none in v1 (input + one node each, sequential). No field written by parallel branches.

## Target ownership (mirrors the docstring in `app/orchestration/state.py`)

### Assist state v2 (`AssistState`, `SCHEMA_VERSION = 2`, thread `assist:<dispute_id>`)

| Field | Owner | Readers | Reducer | Persisted | Sensitive rule |
|---|---|---|---|---|---|
| `schema_version` | `upgrade_state` | tests, admin history | replace | yes | — |
| `dispute`, `transaction`, `evidence_kinds`, `question` | input | nodes as v1 | replace | yes | `transaction`, `question`: redact in logs |
| `tenant_id` | *legacy input, not read* | none | replace | yes (old threads) | kept only so v1 checkpoints load; tenant comes from `Ctx` |
| `reason_family` | `classify_reason` | evidence subgraph, draft | replace | yes | — |
| `evidence` | `retrieve_policy`, `gather_txn_evidence` | grade, draft | `merge_evidence` | yes | excerpt ≤ 400 chars; redact in logs |
| `evidence_grade`, `retrieval_attempts`, `queries` | evidence subgraph | routers, draft | replace | yes | — |
| `policy_hits` | *legacy, no writer in v2* | none | replace | yes (old threads) | dropped from new checkpoints; draft reads `evidence` |
| `recommendation`, `suggested_resolution`, `citations` | `draft_recommendation` | API | replace | yes | `recommendation`: redact in logs |
| `history` | `draft_recommendation` | API | `operator.add` | yes | redact in logs |

### Resolution state v1 (`ResolutionState`, `SCHEMA_VERSION = 1`, thread `resolution:<dispute_id>:<n>`)

| Field | Owner | Readers | Reducer | Sensitive rule |
|---|---|---|---|---|
| `dispute_id`, `requested_by`, `instructions` | input | `load_case`; `approve` (four-eyes) | replace | `instructions`: redact in logs |
| `schema_version` | `load_case` | upgrade hook, tests | replace | — |
| `case` | `load_case` | all later nodes | replace | redact `transaction` in logs |
| `reason_family` | `classify_reason` | evidence subgraph, draft, verify | replace | — |
| `queries`, `retrieval_attempts` | `plan_queries` | retrieve, `route_after_grade` | replace | — |
| `evidence` | `retrieve_policy`, `gather_txn_evidence` (parallel) | grade, draft, verify, execute | `merge_evidence` | reference + excerpt only |
| `evidence_grade` | `grade_evidence` | `route_after_grade`, `escalate_insufficient` | replace | — |
| `draft` | `draft_resolution` | verify, prepare, API | replace | rationale: redact in logs |
| `verification`, `refine_attempts` | `verify` | `route_after_verify`, draft (feedback) | replace | — |
| `action` | `prepare_action` | `route_approval`, approve, execute | replace | — |
| `approval` | `approve` / `request_approval` | execute, record nodes, API | replace | note: redact in logs |
| `execution` | `execute` | `apply_resolution`, API | replace | — |
| `outcome`, `outcome_reason` | terminal nodes (mutually exclusive paths) | API | `write_once` | — |

No `trail` field: the step trail for admins comes from checkpoint history (`aget_state_history`), so nodes do
not write bookkeeping into state.
