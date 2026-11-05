"""Graph state contracts (stage 5): input/output schemas, ownership, reducers, sensitivity and versions.

Values are JSON-safe (str, int, float, bool, None, list, dict) so checkpoints serialise under strict msgpack and
stay readable by later releases. Money travels as strings with 2 dp. Identity is never in state (see Ctx).

Ownership — resolution graph (thread `resolution:<dispute_id>:<n>`, SCHEMA_VERSION["resolution"] = 1)

    field             owner                                  readers                         reducer       sensitive
    dispute_id        input                                  load_case                       replace       no
    requested_by      input                                  approve (four-eyes)             replace       no
    instructions      input                                  plan_queries, draft_resolution  replace       redact
    schema_version    load_case                              upgrade hook, tests             replace       no
    case              load_case                              every later node                replace       redact
    reason_family     classify_reason                        evidence, draft, verify         replace       no
    queries           gather_evidence (wrapped subgraph)     admin history                   replace       no
    retrieval_attempts gather_evidence                       escalate_insufficient           replace       no
    evidence          gather_evidence                        draft, verify, execute          replace       reference
    evidence_grade    gather_evidence                        route_after_evidence, escalate  replace       no
    draft             draft_resolution                       verify, prepare_action, API     replace       redact
    verification      verify                                 route_after_verify, draft       replace       no
    refine_attempts   verify                                 route_after_verify              replace       no
    action            prepare_action                         route_approval, approve, exec   replace       no
    approval          request_approval, approve              execute, record_*, API          replace       redact
    execution         execute                                apply_resolution, API           replace       no
    outcome           terminal nodes (exclusive paths)       API                             write_once    no
    outcome_reason    terminal nodes (exclusive paths)       API                             write_once    no

Inside the evidence subgraph (EvidenceState) `evidence` has two parallel writers — `retrieve_policy` (one `Send`
per query) and `gather_txn_evidence` — and uses `merge_evidence`. The wrapper node copies the merged result into
the parent, where it has one writer per run, so the parent field replaces (an assist turn never inherits the previous
turn's evidence).

Ownership — assist graph v2 (thread `assist:<dispute_id>`, SCHEMA_VERSION["assist"] = 2): see
docs/state_inventory.md; `history` keeps `operator.add` so v1 threads keep counting turns, and the legacy keys
`tenant_id` and `policy_hits` stay declared (never read) so v1 checkpoints load.

Size rule (T05.9): evidence items carry an id, scope, kind, title, score and an excerpt of at most
EXCERPT_CHARS; full documents are fetched by id when a node needs them. One resolution run stays < 64 KB.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired, TypedDict

from app.orchestration.reducers import merge_evidence, write_once

SCHEMA_VERSION = {"assist": 2, "resolution": 1}
EXCERPT_CHARS = 400


class Evidence(TypedDict):
    id: str  # "policy:<chunk_id>" | "ledger:<fact>" | "merchant:<evidence_id>"
    source: str  # policy | ledger | merchant
    scope: str | None  # network | tenant (policy only)
    kind: str  # network_rule | merchant_policy | three_ds_result | avs_cvv_result | delivery_proof | ...
    title: str
    excerpt: str
    score: float


class EvidenceGrade(TypedDict):
    sufficient: bool
    missing: list[str]
    best_score: float
    can_retry: bool  # retrieval_attempts < max; lets the router stay a pure function of state


class Draft(TypedDict):
    resolution: str
    amount: str | None
    currency: str
    rationale: str
    citations: list[str]


class Verification(TypedDict):
    passed: bool
    violations: list[str]  # R1..R6 codes with a short reason, e.g. "R1: amount 999.00 > disputed 640.00"
    can_refine: bool  # failed and refine budget left


class Action(TypedDict):
    kind: str  # refund | representment | request_info
    tool: str | None  # payments.issue_refund | network.submit_representment | None
    amount: str | None
    currency: str
    idempotency_key: str | None
    required_role: str | None
    priority: str  # normal | urgent
    urgent: bool
    auto: bool  # tenant auto-approve applies (never for CONTEST or urgent)
    expires_at: str | None


class Approval(TypedDict):
    approval_id: str
    status: str  # pending | approved | rejected | expired | auto
    decided_by: str | None
    note: str | None


class Execution(TypedDict):
    status: str  # succeeded | failed | unknown
    external_ref: str | None
    attempts: int
    error: str | None


# ---- resolution graph -----------------------------------------------------------------------------------------


class ResolutionIn(TypedDict):
    dispute_id: str
    requested_by: str
    instructions: NotRequired[str]


class ResolutionOut(TypedDict, total=False):
    outcome: str
    outcome_reason: str | None
    draft: Draft
    action: Action
    approval: Approval
    execution: Execution


class ResolutionState(TypedDict, total=False):
    dispute_id: str
    requested_by: str
    instructions: str
    schema_version: int
    case: dict[str, Any]
    reason_family: str
    queries: list[str]
    retrieval_attempts: int
    evidence: list[Evidence]
    evidence_grade: EvidenceGrade
    draft: Draft
    verification: Verification
    refine_attempts: int
    action: Action
    approval: Approval
    execution: Execution
    outcome: Annotated[str, write_once]
    outcome_reason: Annotated[str, write_once]


# ---- evidence subgraph (shared by assist v2 and resolution; wrapped, see docs/graph_design.md) ----------------


class EvidenceState(TypedDict, total=False):
    network: str
    reason_code: str
    reason_family: str
    question: str
    facts: dict[str, Any]  # ledger facts + merchant evidence kinds from the case
    queries: list[str]
    retrieval_attempts: int
    evidence: Annotated[list[Evidence], merge_evidence]
    evidence_grade: EvidenceGrade


# ---- assist graph v2 ------------------------------------------------------------------------------------------


class AssistStateV2(TypedDict, total=False):
    schema_version: int
    tenant_id: str  # legacy (v1 input), never read
    policy_hits: list[dict[str, Any]]  # legacy (v1), never written by v2
    dispute: dict[str, Any]
    transaction: dict[str, Any]
    evidence_kinds: list[str]
    question: str
    reason_family: str
    queries: list[str]
    retrieval_attempts: int
    evidence: list[Evidence]
    evidence_grade: EvidenceGrade
    recommendation: str
    suggested_resolution: str
    citations: list[str]
    history: Annotated[list[dict[str, str]], operator.add]


# ---- ownership, sensitivity (consumed by tests/unit/test_state_ownership.py and the log redactor) --------------

INPUT = "input"

OWNERS: dict[str, dict[str, set[str]]] = {
    "resolution": {
        INPUT: {"dispute_id", "requested_by", "instructions"},
        "load_case": {"schema_version", "case"},
        "classify_reason": {"reason_family"},
        "gather_evidence": {"queries", "retrieval_attempts", "evidence", "evidence_grade"},
        "draft_resolution": {"draft"},
        "verify": {"verification", "refine_attempts"},
        "prepare_action": {"action"},
        "request_approval": {"approval"},
        "approve": {"approval"},
        "execute": {"execution"},
        "close_expired": {"outcome", "outcome_reason"},
        "escalate_insufficient": {"outcome", "outcome_reason"},
        "escalate_unverified": {"outcome", "outcome_reason"},
        "request_info": {"outcome", "outcome_reason"},
        "record_rejection": {"outcome", "outcome_reason"},
        "record_expiry": {"outcome", "outcome_reason"},
        "record_failure": {"outcome", "outcome_reason"},
        "apply_resolution": {"outcome", "outcome_reason"},
    },
    "assist": {
        INPUT: {"tenant_id", "dispute", "transaction", "evidence_kinds", "question"},
        "legacy": {"policy_hits"},
        "upgrade_state": {"schema_version"},
        "classify_reason": {"reason_family"},
        "gather_evidence": {"queries", "retrieval_attempts", "evidence", "evidence_grade"},
        "draft_recommendation": {"recommendation", "suggested_resolution", "citations", "history"},
    },
    "evidence": {
        INPUT: {"network", "reason_code", "reason_family", "question", "facts"},
        "plan_queries": {"queries", "retrieval_attempts"},
        "retrieve_policy": {"evidence"},
        "gather_txn_evidence": {"evidence"},
        "grade_evidence": {"evidence_grade"},
    },
}

# Fields that may legitimately have more than one owner, and the rule that makes that safe.
MULTI_WRITER: dict[str, str] = {
    "evidence": "merge_evidence (parallel branches inside the evidence subgraph)",
    "approval": "request_approval then approve: sequential, same path",
    "outcome": "write_once (mutually exclusive terminal paths)",
    "outcome_reason": "write_once (mutually exclusive terminal paths)",
}

# T05.7: how each sensitive field is treated outside the checkpoint.
SENSITIVE: dict[str, str] = {
    "instructions": "redact_in_logs",
    "question": "redact_in_logs",
    "case": "redact_in_logs",
    "transaction": "redact_in_logs",
    "draft": "redact_in_logs",
    "recommendation": "redact_in_logs",
    "history": "redact_in_logs",
    "approval": "redact_in_logs",
    "evidence": "reference_only",
}

# Never allowed in any state schema (SR3, SR6).
FORBIDDEN_KEYS = {"user_id", "role", "request_id", "api_key", "token", "password", "pan", "card_number"}
