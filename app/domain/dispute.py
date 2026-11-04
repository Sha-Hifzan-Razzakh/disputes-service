"""Dispute aggregate: lifecycle and invariants."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.errors import DomainError, InvalidTransition
from app.domain.money import Money
from app.domain.reason_codes import RESPONSE_WINDOW_DAYS, Network, ReasonCode


class DisputeStatus(StrEnum):
    OPENED = "OPENED"
    EVIDENCE_GATHERING = "EVIDENCE_GATHERING"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED_ACCEPTED = "RESOLVED_ACCEPTED"
    RESOLVED_CONTESTED = "RESOLVED_CONTESTED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"


TERMINAL = {
    DisputeStatus.RESOLVED_ACCEPTED,
    DisputeStatus.RESOLVED_CONTESTED,
    DisputeStatus.WITHDRAWN,
    DisputeStatus.EXPIRED,
}

# action -> (allowed source statuses, target status)
TRANSITIONS: dict[str, tuple[frozenset[DisputeStatus], DisputeStatus]] = {
    "start_evidence": (frozenset({DisputeStatus.OPENED, DisputeStatus.UNDER_REVIEW}), DisputeStatus.EVIDENCE_GATHERING),
    "submit_for_review": (frozenset({DisputeStatus.EVIDENCE_GATHERING}), DisputeStatus.UNDER_REVIEW),
    "resolve_accept": (frozenset({DisputeStatus.UNDER_REVIEW}), DisputeStatus.RESOLVED_ACCEPTED),
    "resolve_contest": (frozenset({DisputeStatus.UNDER_REVIEW}), DisputeStatus.RESOLVED_CONTESTED),
    "withdraw": (frozenset({DisputeStatus.OPENED, DisputeStatus.EVIDENCE_GATHERING}), DisputeStatus.WITHDRAWN),
    "expire": (
        frozenset({DisputeStatus.OPENED, DisputeStatus.EVIDENCE_GATHERING, DisputeStatus.UNDER_REVIEW}),
        DisputeStatus.EXPIRED,
    ),
}


@dataclass(frozen=True, slots=True)
class DisputeEvent:
    kind: str
    actor: str
    at: datetime
    payload: dict[str, object] = field(default_factory=dict)


@dataclass
class Dispute:
    id: uuid.UUID
    tenant_id: str
    network: Network
    reason_code: str
    transaction_ref: str
    amount: Money
    status: DisputeStatus
    opened_at: datetime
    respond_by: datetime
    assigned_to: str | None = None
    version: int = 1
    pending_events: list[DisputeEvent] = field(default_factory=list, repr=False)

    @classmethod
    def open(
        cls,
        *,
        tenant_id: str,
        reason: ReasonCode,
        transaction_ref: str,
        amount: Money,
        transaction_amount: Money,
        actor: str,
        now: datetime,
    ) -> Dispute:
        if not amount <= transaction_amount:
            raise DomainError(f"disputed amount {amount} exceeds transaction amount {transaction_amount}")
        if amount.amount == 0:
            raise DomainError("disputed amount must be positive")
        d = cls(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            network=reason.network,
            reason_code=reason.code,
            transaction_ref=transaction_ref,
            amount=amount,
            status=DisputeStatus.OPENED,
            opened_at=now,
            respond_by=now + timedelta(days=RESPONSE_WINDOW_DAYS[reason.network]),
        )
        d.pending_events.append(
            DisputeEvent(
                "opened",
                actor,
                now,
                {"reason_code": reason.code, "network": reason.network.value, "amount": str(amount)},
            )
        )
        return d

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def assign(self, user_id: str, *, actor: str, now: datetime) -> None:
        if self.is_terminal:
            raise InvalidTransition(self.status, "assign")
        self.assigned_to = user_id
        self._bump("assigned", actor, now, {"assigned_to": user_id})

    def apply(self, action: str, *, actor: str, now: datetime, note: str | None = None) -> None:
        if action not in TRANSITIONS:
            raise DomainError(f"unknown action {action}")
        sources, target = TRANSITIONS[action]
        if self.status not in sources:
            raise InvalidTransition(self.status, action)
        if action in {"resolve_accept", "resolve_contest"} and now > self.respond_by:
            raise DomainError("response deadline has passed")
        previous = self.status
        self.status = target
        self._bump(action, actor, now, {"from": previous.value, "to": target.value, "note": note})

    def _bump(self, kind: str, actor: str, now: datetime, payload: dict[str, object]) -> None:
        self.version += 1
        self.pending_events.append(DisputeEvent(kind, actor, now, payload))
