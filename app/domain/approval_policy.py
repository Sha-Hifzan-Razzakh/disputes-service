"""Approval routing (spec D6/D7): required role, priority, auto-approve, expiry and the idempotency key."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.money import Money
from app.domain.reason_codes import Resolution
from app.domain.roles import ROLE_RANK


@dataclass(frozen=True, slots=True)
class ApprovalRoute:
    required_role: str
    priority: str  # normal | urgent
    urgent: bool
    auto: bool
    expires_at: datetime


def route(
    *,
    resolution: Resolution,
    amount: Money | None,
    threshold: Money,
    auto_approve_below: Money | None,
    respond_by: datetime,
    now: datetime,
    urgent_window: timedelta,
    ttl: timedelta,
    deadline_margin: timedelta,
    min_decision_window: timedelta = timedelta(hours=1),
    execution_buffer: timedelta = timedelta(minutes=15),
) -> ApprovalRoute:
    if resolution is Resolution.REQUEST_INFO:
        raise ValueError("REQUEST_INFO needs no approval")
    refund = resolution in (Resolution.ACCEPT, Resolution.PARTIAL)
    # thresholds are in the tenant's currency; with no FX here, a refund in another currency is treated as large
    # (ops lead, never auto-approved) instead of comparing 450 GBP with 500 USD as numbers (review R9)
    foreign = refund and amount is not None and amount.currency != threshold.currency
    role = "ops_lead" if refund and amount is not None and (foreign or amount.amount >= threshold.amount) else "analyst"
    urgent = respond_by - now < urgent_window
    if urgent and ROLE_RANK[role] < ROLE_RANK["ops_lead"]:
        role = "ops_lead"
    auto = (
        refund
        and not urgent
        and auto_approve_below is not None
        and amount is not None
        and not foreign
        and amount.amount < auto_approve_below.amount
    )
    expires_at = min(now + ttl, respond_by - deadline_margin)
    if expires_at < now + min_decision_window:
        # Close to the deadline the normal margin would leave no time to decide (found by the evals: with <= 24 h
        # left the approval was born expired). Give the approver the time that remains, minus an execution buffer.
        expires_at = respond_by - execution_buffer if respond_by - execution_buffer > now else respond_by
    return ApprovalRoute(role, "urgent" if urgent else "normal", urgent, auto, expires_at)


def idempotency_key(*, tenant_id: str, dispute_id: str, thread_id: str, kind: str, amount: Money | None) -> str:
    """Fixed before the approval pause, so every replay of `execute` sends the same key (G6)."""
    raw = "|".join([tenant_id, dispute_id, thread_id, kind, str(amount.amount) if amount else "-"])
    return hashlib.sha256(raw.encode()).hexdigest()
