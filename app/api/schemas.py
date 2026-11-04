"""Request/response DTOs for the v1 API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.application.ports import EvidenceItem, TimelineEntry, TransactionView
from app.domain.dispute import Dispute


class OpenDisputeIn(BaseModel):
    network: Literal["VISA", "MASTERCARD"]
    reason_code: str = Field(min_length=2, max_length=10)
    transaction_ref: str = Field(min_length=3, max_length=64)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class DisputeOut(BaseModel):
    id: uuid.UUID
    network: str
    reason_code: str
    transaction_ref: str
    amount: Decimal
    currency: str
    status: str
    opened_at: datetime
    respond_by: datetime
    assigned_to: str | None
    version: int

    @classmethod
    def of(cls, d: Dispute) -> DisputeOut:
        return cls(
            id=d.id,
            network=d.network.value,
            reason_code=d.reason_code,
            transaction_ref=d.transaction_ref,
            amount=d.amount.amount,
            currency=d.amount.currency,
            status=d.status.value,
            opened_at=d.opened_at,
            respond_by=d.respond_by,
            assigned_to=d.assigned_to,
            version=d.version,
        )


class DisputePage(BaseModel):
    items: list[DisputeOut]
    limit: int
    offset: int


class DisputeActionIn(BaseModel):
    action: Literal["assign", "start_evidence", "submit_for_review", "resolve_accept", "resolve_contest"]
    expected_version: int = Field(ge=1)
    assign_to: str | None = None
    note: str | None = Field(default=None, max_length=1000)


class EvidenceIn(BaseModel):
    kind: str
    description: str = Field(min_length=3, max_length=4000)
    uri: str | None = Field(default=None, max_length=500)


class EvidenceOut(BaseModel):
    id: uuid.UUID
    kind: str
    description: str
    uri: str | None
    submitted_by: str
    submitted_at: datetime

    @classmethod
    def of(cls, e: EvidenceItem) -> EvidenceOut:
        return cls(
            id=e.id,
            kind=e.kind,
            description=e.description,
            uri=e.uri,
            submitted_by=e.submitted_by,
            submitted_at=e.submitted_at,
        )


class TimelineOut(BaseModel):
    at: datetime
    actor: str
    kind: str
    payload: dict[str, object]

    @classmethod
    def of(cls, t: TimelineEntry) -> TimelineOut:
        return cls(at=t.at, actor=t.actor, kind=t.kind, payload=t.payload)


class AssistIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class AssistOut(BaseModel):
    recommendation: str
    suggested_resolution: str
    citations: list[str]
    turns: int


class TransactionOut(BaseModel):
    ref: str
    amount: Decimal
    currency: str
    captured_at: datetime
    card_brand: str
    avs_result: str | None
    cvv_result: str | None
    three_ds_authenticated: bool
    delivery_status: str | None
    delivery_tracking: str | None
    refunded_amount: Decimal

    @classmethod
    def of(cls, t: TransactionView) -> TransactionOut:
        return cls(
            ref=t.ref,
            amount=t.amount.amount,
            currency=t.amount.currency,
            captured_at=t.captured_at,
            card_brand=t.card_brand,
            avs_result=t.avs_result,
            cvv_result=t.cvv_result,
            three_ds_authenticated=t.three_ds_authenticated,
            delivery_status=t.delivery_status,
            delivery_tracking=t.delivery_tracking,
            refunded_amount=t.refunded.amount,
        )


class PolicyDocIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=40, max_length=200_000)
    scope: Literal["tenant", "network"] = "tenant"


class PolicyDocOut(BaseModel):
    chunks: int
    scope: str
