"""Ports (interfaces) and the plain data shapes that cross them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.dispute import Dispute
from app.domain.money import Money


@dataclass(frozen=True, slots=True)
class TransactionView:
    ref: str
    amount: Money
    captured_at: datetime
    card_brand: str
    avs_result: str | None
    cvv_result: str | None
    three_ds_authenticated: bool
    delivery_status: str | None
    delivery_tracking: str | None
    refunded: Money
    description: str | None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: uuid.UUID
    dispute_id: uuid.UUID
    tenant_id: str
    kind: str
    description: str
    uri: str | None
    submitted_by: str
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    at: datetime
    actor: str
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class PolicyHit:
    chunk_id: str
    document_title: str
    text: str
    score: float
    scope: str  # "tenant" | "network"


@dataclass(frozen=True, slots=True)
class Recommendation:
    text: str
    suggested_resolution: str
    citations: list[str] = field(default_factory=list)


class DisputeRepository(Protocol):
    async def get(self, tenant_id: str, dispute_id: uuid.UUID) -> Dispute: ...
    async def page(self, tenant_id: str, *, status: str | None, limit: int, offset: int) -> Sequence[Dispute]: ...
    async def add(self, d: Dispute) -> None: ...
    async def save(self, d: Dispute, *, expected_version: int) -> None: ...
    async def timeline(self, tenant_id: str, dispute_id: uuid.UUID) -> list[TimelineEntry]: ...


class EvidenceRepository(Protocol):
    async def add(self, item: EvidenceItem) -> None: ...
    async def for_dispute(self, tenant_id: str, dispute_id: uuid.UUID) -> list[EvidenceItem]: ...


class Ledger(Protocol):
    async def get_transaction(self, tenant_id: str, ref: str) -> TransactionView: ...


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class PolicyStore(Protocol):
    async def add_document(self, *, tenant_id: str | None, title: str, source: str, text: str) -> int: ...
    async def search(self, *, tenant_id: str, query: str, k: int) -> list[PolicyHit]: ...


class RecommendationDrafter(Protocol):
    async def draft(self, *, case: dict[str, object], question: str, hits: list[PolicyHit]) -> Recommendation: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
