# Behavioural specification — dispute resolution workflow (v2)

Stage 2 output (T02.1, T02.3, T02.4, T02.6). Source of truth for `specs/*.feature`, `evals/golden_v0.jsonl`
and the outcome tests. Rule texts and numbers are this service's own illustrative values, not card-network
rulebook quotations.

## 1. Problem

Analysts at each merchant work disputes by hand: they read the transaction, look up the network rule and the
merchant's policy, decide to refund or contest, then issue refunds in the payments console and upload
representments to the network portal. v1 added an assistant that *suggests* a resolution, but:

- suggestions are not checked against the evidence or the rules (a suggestion can cite nothing, or exceed the
  disputed amount);
- retrieval has no retry and no "not enough evidence" answer;
- execution happens outside the system, so there is no approval trail, no idempotency and no link between the
  decision and the money movement.

v2 adds a **resolution workflow**: the system gathers evidence, drafts a resolution, verifies it, asks an
authorised person to approve it, then executes it exactly once and records the result on the dispute.

## 2. Actors

| Actor | Role claim | Does |
|---|---|---|
| Analyst | `analyst` | Starts a resolution; approves CONTEST and low-value refunds raised by someone else |
| Ops lead | `ops_lead` | Approves refunds at or above the tenant threshold and all urgent items |
| Admin | `admin` | Everything an ops lead can do; maintains tenant dispute policy |
| Payments system | — (MCP server `payments`) | `get_transaction`, `issue_refund` |
| Card network gateway | — (MCP server `network-gateway`) | `submit_representment` |
| Expiry sweeper | service identity `system:sweeper` | Expires approvals past their TTL |

## 3. Inputs

| Input | Source | Notes |
|---|---|---|
| `dispute_id` | path | Must belong to the caller's tenant |
| `instructions` | body, optional, ≤ 1 000 chars | Free text from the analyst, e.g. "customer says box was empty" |
| Identity | JWT (`sub`, `tenant`, `role`) + `X-Request-ID` | Goes to `Ctx`, never to state |
| Dispute, transaction, merchant evidence | DB (tenant-scoped) | Snapshot taken at `load_case` |
| Network rules + merchant policies | policy store | Retrieved per run; network docs shared, merchant docs tenant-scoped |
| Tenant dispute policy | `tenant_dispute_policy` table | Approval threshold, auto-approve limit (off by default) |

## 4. Decisions (if / then / else)

D1 **Deadline.** If `now ≥ respond_by` at load → mark the dispute EXPIRED, outcome `DEADLINE_PASSED`, stop.

D2 **Evidence sufficiency.** After each retrieval round, grade the evidence for the reason family:
- FRAUD needs the ledger authentication facts (3-D Secure, AVS, CVV) and at least one network-rule passage.
- NOT_RECEIVED needs the delivery status and tracking, and at least one network-rule passage.
- NOT_AS_DESCRIBED needs at least one merchant-policy passage and at least one network-rule passage.
- PROCESSING_ERROR needs the refund history and at least one network-rule passage.
- A passage counts only if its score is at or above `APP_MIN_EVIDENCE_SCORE` (default 0.12).

If sufficient → draft. Else if `retrieval_attempts < 2` → re-plan queries (broaden) and retrieve again.
Else → outcome `ABSTAINED_INSUFFICIENT_EVIDENCE`, list what is missing.

D3 **Draft.** Choose ACCEPT, PARTIAL, CONTEST or REQUEST_INFO with amount, rationale and citations.

D4 **Verification.** The draft passes only if all hold:
- R1 amount > 0 and ≤ disputed amount; PARTIAL amount < disputed amount;
- R2 resolution is allowed for the reason code;
- R3 `now < respond_by`;
- R4 prior refunds + this refund ≤ transaction amount;
- R5 CONTEST cites at least one item of compelling evidence for the family (see D2);
- R6 at least one citation, and every citation is an evidence id gathered in this run.

If it passes → prepare the action. Else if `refine_attempts < 1` → redraft with the violations as feedback.
Else → outcome `ABSTAINED_FAILED_VERIFICATION`, list the violations.

D5 **Action.** ACCEPT → refund the disputed amount. PARTIAL → refund the drafted amount. CONTEST →
submit a representment with the cited evidence. REQUEST_INFO → no external action: notify the merchant's
analysts, move the dispute to EVIDENCE_GATHERING if needed, outcome `INFO_REQUESTED`.

D6 **Approval route.** Every ACCEPT / PARTIAL / CONTEST needs one approval.
- Required role: CONTEST → `analyst`; refund amount < tenant threshold → `analyst`; refund ≥ threshold → `ops_lead`.
- If hours to `respond_by` < 48 → urgent: required role becomes at least `ops_lead`, queue priority `urgent`.
- If the tenant enabled auto-approve and the refund is below its auto-approve limit and not urgent → approve
  as `system:auto` (off by default; never for CONTEST).
- The approver must not be the requester (four-eyes). `admin` satisfies any required role.

D7 **Approval decision.** Approved → execute. Rejected → outcome `REJECTED`, dispute unchanged.
Not decided before TTL = min(created + 72 h, `respond_by` − 24 h) → outcome `APPROVAL_EXPIRED`.

D8 **Execution.** Call the tool with the idempotency key fixed before the approval. Transient errors (timeout,
5xx, rate limit) retry up to 3 times with backoff. On success → apply the domain transition
(`resolve_accept` / `resolve_contest`) with optimistic concurrency, write the outbox notification, outcome
`REFUNDED`, `PARTIALLY_REFUNDED` or `REPRESENTED`. On a permanent error or exhausted retries → outcome
`EXECUTION_FAILED`, side effect marked `unknown` for reconciliation, ops alerted.

### Expected resolution by family (what the golden set encodes)

| Family | Facts | Expected draft |
|---|---|---|
| FRAUD | 3-D Secure authenticated | CONTEST (liability shift) |
| FRAUD | No 3DS, AVS = Y and CVV = M, merchant has `customer_comms` or `transaction_records` | CONTEST |
| FRAUD | No 3DS, AVS = Y and CVV = M, nothing else | REQUEST_INFO |
| FRAUD | Otherwise | ACCEPT |
| NOT_RECEIVED | Delivered with tracking | CONTEST |
| NOT_RECEIVED | Delivered without tracking | REQUEST_INFO |
| NOT_RECEIVED | In transit or no delivery record | ACCEPT |
| NOT_AS_DESCRIBED | `product_description` and `customer_comms` on file | CONTEST |
| NOT_AS_DESCRIBED | `product_description` only | PARTIAL, 50 % of disputed (half-even to cents) |
| NOT_AS_DESCRIBED | Neither | REQUEST_INFO |
| PROCESSING_ERROR | Prior refunds ≥ disputed amount | CONTEST (credit already issued) |
| PROCESSING_ERROR | Otherwise | ACCEPT |

Analyst `instructions` add context for the drafter and the notification text; they never change a rule,
the required role or the approval step (golden case G30).

## 5. Outcome table (T02.3)

| # | Outcome (`outcome`) | Class | Trigger | User sees | Human? |
|---|---|---|---|---|---|
| O1 | `REFUNDED` | terminate | ACCEPT approved and refund succeeded | 201 / final status, refund id, dispute RESOLVED_ACCEPTED | approval |
| O2 | `PARTIALLY_REFUNDED` | terminate | PARTIAL approved and partial refund succeeded | as O1 with partial amount | approval |
| O3 | `REPRESENTED` | terminate | CONTEST approved and representment accepted by gateway | submission id, dispute RESOLVED_CONTESTED | approval |
| O4 | `INFO_REQUESTED` | ask for input | Draft is REQUEST_INFO | list of missing evidence kinds; dispute EVIDENCE_GATHERING | no |
| O5 | `ABSTAINED_INSUFFICIENT_EVIDENCE` | abstain | D2 exhausted (2 rounds) | "not enough evidence" + missing items | no (analyst decides by hand) |
| O6 | `ABSTAINED_FAILED_VERIFICATION` | abstain | D4 failed after 1 refine | violations R1–R6 | no |
| O7 | `REJECTED` | terminate | Approver rejected | rejection note | yes |
| O8 | `APPROVAL_EXPIRED` | terminate | TTL passed with no decision | expiry time | no (sweeper) |
| O9 | `DEADLINE_PASSED` | terminate | D1 | dispute EXPIRED | no |
| O10 | `EXECUTION_FAILED` | escalate | Permanent tool error or retries exhausted | failure + reconciliation ticket; dispute unchanged | ops follow-up |
| O11 | `AWAITING_APPROVAL` | pause (escalate to human) | D6 needs a human | 202 with `approval_id`, required role, priority | yes |

Urgent routing (D6) is a modifier on O11, not its own outcome. Request-level outcomes handled by the API:

| # | Condition | HTTP |
|---|---|---|
| A1 | A resolution for this dispute is already running or awaiting approval | 409 `resolution_in_progress` |
| A2 | Dispute is not EVIDENCE_GATHERING or UNDER_REVIEW | 409 `invalid_transition` |
| A3 | Approver lacks the required role, or is the requester | 403 `forbidden` |
| A4 | Approval already decided | 409 `approval_decided` |
| A5 | Approval expired | 410 `approval_expired` |
| A6 | Dispute, thread or approval of another tenant | 404 `not_found` |
| A7 | Run exceeds `APP_RUN_TIMEOUT_S` | 504 `run_timeout`; the thread stays resumable |

## 6. Must / May (T02.4)

**Must** (business rules — changing one needs the product owner):
- M1 No refund or representment without an approval from an allowed role who is not the requester (auto-approve
  is the only exception and is off by default).
- M2 At most one refund or representment per approved action, whatever retries or restarts happen.
- M3 Never refund more than disputed, and never more than transaction amount minus prior refunds.
- M4 Never act after `respond_by`.
- M5 Every recommendation cites evidence gathered in the same run; no citation → no action.
- M6 No data, retrieval or tool call crosses tenants.
- M7 Every approval decision and every side effect is auditable (who, role, when, request id).
- M8 v1 `/v1/disputes/{id}/assist` keeps its contract and existing threads keep their history.

**May** (implementation choices — open to the engineer):
- The drafter may be rule-based or an LLM behind the `ResolutionDrafter` port.
- Retrieval may be pgvector or the in-process cosine fallback behind `PolicyStore`.
- Tools may be called over MCP stdio, streamable HTTP or in-process, behind `ToolGateway`.
- Streaming may be SSE or polling.
- The expiry sweeper may be an in-process task or an external scheduled job.

## 7. Quality attributes (T02.6)

| Attribute | Scenario (stimulus → response) | Measure | Target | Tool |
|---|---|---|---|---|
| Latency (start) | Analyst starts a resolution → 202 with approval or a terminal outcome | p95 server time | ≤ 3 s rules drafter; ≤ 12 s LLM drafter | pytest timing in eval run; `/metrics` histogram |
| Latency (execute) | Approver approves → refund recorded | p95 | ≤ 5 s with gateway p95 ≤ 2 s | same |
| Cost | One resolution run with LLM drafter | model spend | ≤ 0.03 USD, ≤ 8 000 tokens | eval runner cost scorer |
| Decision quality | Golden set | outcome + resolution match | ≥ 90 % | `evals/run.py` |
| Abstention | Golden cases labelled "should abstain" | precision / recall | ≥ 0.9 / ≥ 0.8 | eval runner |
| Groundedness | Every executed action | citations ⊆ gathered evidence | 100 % (rule R6) | verify node + eval |
| Faithfulness | LLM drafts | Ragas faithfulness | ≥ 0.85 | Ragas (runs only with an API key) |
| Isolation | Any request | cross-tenant reads | 0 | isolation tests + eval metric |
| Side-effect safety | Crash after tool call / double approve / retry | duplicate refunds | 0 | chaos tests |
| Resumability | Process restart while awaiting approval or mid-run | runs resumable | 100 %; v1 fixture resumes | compat + chaos tests |
| Auditability | Every decision / side effect | rows in `audit_log` | 100 % | API tests |
| Availability | `/v2` endpoints | monthly success ratio (non-5xx) | ≥ 99.5 % | SLO rule (stage 25) |
| Bounded runtime | Any run | API timeout; recursion limit | 30 s → 504; `recursion_limit` 40 | settings + tests |
