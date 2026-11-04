# Evals

`golden_v0.jsonl` — 32 labelled cases written from `docs/spec.md` (T02.7) before any v2 code exists.
Each line is self-contained so the eval runner (stage 17) can create the transaction and dispute in a
dedicated `evalco` tenant, run the workflow to its first stop, and compare.

| Field | Meaning |
|---|---|
| `id` | `G01`…; stable, referenced from tests and the PR |
| `network`, `reason_code`, `disputed_amount` | The dispute |
| `transaction` | Ledger facts: amount, 3DS, AVS, CVV, delivery status/tracking, prior refunds |
| `merchant_evidence` | Evidence kinds the merchant uploaded |
| `days_to_deadline` | Fraction of days until `respond_by` (negative = already passed) |
| `instructions` | Analyst free text, may be adversarial (G30) |
| `fixture` | Optional test double: `empty_policy_store`, `network_docs_only`, `low_scores_first_round`, `drafter_overpays`, `drafter_forgets_citations_once` |
| `expected.outcome` | First stop of the run: `AWAITING_APPROVAL` or a terminal outcome |
| `expected.resolution`, `amount`, `required_role`, `priority` | Draft and approval route when awaiting approval |
| `expected.violations`, `retrieval_attempts` | For abstentions |

Coverage: 4 families × accept / partial / contest / request-info, threshold boundary (G07, G08), half-even
rounding (G18), prior refunds (G22, G32), verification refine (G23, G24), abstention (G25, G26), retry
(G27), deadline (G28), urgency (G13, G29), prompt injection (G30).
