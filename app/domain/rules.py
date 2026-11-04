"""Resolution rules (spec D2, D4, D5): evidence sufficiency, draft verification R1-R6, partial amounts.

Pure functions over plain data so routers, nodes and tests can share them. Evidence items are mappings with
`id`, `source` (policy | ledger | merchant), `scope`, `kind` and `score`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.domain.money import Money
from app.domain.reason_codes import ReasonCode, ReasonFamily, Resolution

# Ledger facts each family must have looked at (ids produced by application.evidence.ledger_evidence).
REQUIRED_LEDGER_FACTS: dict[ReasonFamily, frozenset[str]] = {
    ReasonFamily.FRAUD: frozenset({"ledger:three_ds", "ledger:avs_cvv"}),
    ReasonFamily.NOT_RECEIVED: frozenset({"ledger:delivery"}),
    ReasonFamily.NOT_AS_DESCRIBED: frozenset(),
    ReasonFamily.PROCESSING_ERROR: frozenset({"ledger:refunds"}),
}

# Evidence kinds that count as compelling for a CONTEST (R5). Ledger facts only carry these kinds when the fact
# favours the merchant (e.g. "three_ds_result" only when 3-D Secure authenticated).
COMPELLING_KINDS: dict[ReasonFamily, frozenset[str]] = {
    ReasonFamily.FRAUD: frozenset({"three_ds_result", "avs_cvv_result", "customer_comms", "transaction_records"}),
    ReasonFamily.NOT_RECEIVED: frozenset({"delivery_proof"}),
    ReasonFamily.NOT_AS_DESCRIBED: frozenset({"product_description", "customer_comms"}),
    ReasonFamily.PROCESSING_ERROR: frozenset({"refund_record", "transaction_records"}),
}


@dataclass(frozen=True, slots=True)
class Grade:
    sufficient: bool
    missing: tuple[str, ...]
    best_score: float


def grade_evidence(family: ReasonFamily, evidence: Iterable[Mapping[str, Any]], *, min_score: float) -> Grade:
    """D2: the family's ledger facts were gathered, plus at least one network-rule passage at or above the minimum
    score; NOT_AS_DESCRIBED also needs a merchant-policy passage."""
    items = list(evidence)
    ids = {e["id"] for e in items}
    policy = [e for e in items if e["source"] == "policy"]
    best = max((float(e["score"]) for e in policy), default=0.0)
    missing = sorted(REQUIRED_LEDGER_FACTS[family] - ids)
    if not any(e["scope"] == "network" and e["score"] >= min_score for e in policy):
        missing.append("network_rule")
    if family is ReasonFamily.NOT_AS_DESCRIBED and not any(
        e["scope"] == "tenant" and e["score"] >= min_score for e in policy
    ):
        missing.append("merchant_policy")
    return Grade(sufficient=not missing, missing=tuple(missing), best_score=round(best, 4))


def partial_amount(disputed: Money, pct: int) -> Money:
    """Spec: PARTIAL refunds `pct` % of the disputed amount, half-even to cents."""
    return Money.of(disputed.amount * Decimal(pct) / Decimal(100), disputed.currency)


def verify_draft(
    *,
    resolution: str,
    amount: Money | None,
    citations: Iterable[str],
    reason: ReasonCode,
    disputed: Money,
    transaction_amount: Money,
    refunded: Money,
    respond_by: datetime,
    now: datetime,
    evidence: Iterable[Mapping[str, Any]],
) -> list[str]:
    """D4 rules R1-R6. Returns violations as 'Rn: reason'; empty list = passed."""
    v: list[str] = []
    items = {e["id"]: e for e in evidence}
    cites = list(citations)
    try:
        res = Resolution(resolution)
    except ValueError:
        return [f"R2: unknown resolution {resolution!r}"]
    refunds = res in (Resolution.ACCEPT, Resolution.PARTIAL)
    if refunds:
        if amount is None or amount.amount <= 0:
            v.append("R1: refund amount must be positive")
        elif amount.currency != disputed.currency:
            v.append(f"R1: currency {amount.currency} differs from dispute currency {disputed.currency}")
        elif amount.amount > disputed.amount:
            v.append(f"R1: amount {amount} exceeds disputed {disputed}")
        elif res is Resolution.ACCEPT and amount.amount != disputed.amount:
            v.append(f"R1: ACCEPT refunds the disputed amount {disputed}, not {amount}")
        elif res is Resolution.PARTIAL and amount.amount >= disputed.amount:
            v.append(f"R1: partial amount {amount} must be below disputed {disputed}")
    if res not in reason.allowed:
        v.append(f"R2: {res.value} not allowed for {reason.network.value} {reason.code}")
    if now >= respond_by:
        v.append("R3: response deadline has passed")
    if refunds and amount is not None and amount.currency == refunded.currency:
        if refunded.amount + amount.amount > transaction_amount.amount:
            v.append(f"R4: prior refunds {refunded} + {amount} exceed transaction {transaction_amount}")
    if res is Resolution.CONTEST and not any(
        items.get(c, {}).get("kind") in COMPELLING_KINDS[reason.family] for c in cites
    ):
        v.append("R5: contest cites no compelling evidence for the reason family")
    if res is not Resolution.REQUEST_INFO:
        if not cites:
            v.append("R6: no citations")
        elif unknown := [c for c in cites if c not in items]:
            v.append(f"R6: citations not gathered in this run: {', '.join(sorted(unknown))}")
    return v
