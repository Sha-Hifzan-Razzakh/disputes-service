# ADR-0003: No cross-thread memory store

- Status: Accepted · Date: 2026-11-04 · Stage: 5 (T05.6)

## Context
LangGraph offers a `BaseStore` for memory shared across threads (e.g. "what this analyst usually decides").
Candidates here: tenant preferences, past outcomes for the same cardholder, analyst feedback.

## Decision
No store. Everything that outlives a thread is a business record with its own table and owner: tenant policy
(`tenant_dispute_policy`), approvals, side effects, audit, the dispute timeline. Per-dispute conversation stays
in the thread (`assist:<id>`).

## Consequences
- No hidden state that influences decisions without an audit trail; rules stay in `domain/`.
- Learning from past outcomes, if ever wanted, becomes an explicit feature with an eval, not a memory side channel.
- Revisit if analysts ask for personal shortcuts that are not business rules.
