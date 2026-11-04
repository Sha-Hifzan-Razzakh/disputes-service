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
) -> ApprovalRoute:
    if resolution is Resolution.REQUEST_INFO:
        raise ValueError("REQUEST_INFO needs no approval")
    refund = resolution in (Resolution.ACCEPT, Resolution.PARTIAL)
    role = "ops_lead" if refund and amount is not None and amount.amount >= threshold.amount else "analyst"
    urgent = respond_by - now < urgent_window
    if urgent and ROLE_RANK[role] < ROLE_RANK["ops_lead"]:
        role = "ops_lead"
    auto = (
        refund
        and not urgent
        and auto_approve_below is not None
        and amount is not None
        and amount.amount < auto_approve_below.amount
    )
    expires_at = min(now + ttl, respond_by - deadline_margin)
    return ApprovalRoute(role, "urgent" if urgent else "normal", urgent, auto, expires_at)


def idempotency_key(*, tenant_id: str, dispute_id: str, thread_id: str, kind: str, amount: Money | None) -> str:
    """Fixed before the approval pause, so every replay of `execute` sends the same key (G6)."""
    raw = "|".join([tenant_id, dispute_id, thread_id, kind, str(amount.amount) if amount else "-"])
    return hashlib.sha256(raw.encode()).hexdigest()
