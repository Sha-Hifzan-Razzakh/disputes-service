"""SQLAlchemy ORM tables. Every tenant-owned table carries tenant_id and is always filtered by it."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TransactionRow(Base):
    """Mirror of the payments ledger, refreshed by an upstream feed (read-only to this service)."""

    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "ref"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    ref: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    card_brand: Mapped[str] = mapped_column(String(20))
    avs_result: Mapped[str | None] = mapped_column(String(8))
    cvv_result: Mapped[str | None] = mapped_column(String(8))
    three_ds_authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_status: Mapped[str | None] = mapped_column(String(32))
    delivery_tracking: Mapped[str | None] = mapped_column(String(64))
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    description: Mapped[str | None] = mapped_column(Text)


class DisputeRow(Base):
    __tablename__ = "disputes"
    __table_args__ = (Index("ix_disputes_tenant_status", "tenant_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    network: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str] = mapped_column(String(10))
    transaction_ref: Mapped[str] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    respond_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DisputeEventRow(Base):
    __tablename__ = "dispute_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispute_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disputes.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class EvidenceRow(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dispute_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disputes.id"), index=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    uri: Mapped[str | None] = mapped_column(String(500))
    submitted_by: Mapped[str] = mapped_column(String(64))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PolicyDocumentRow(Base):
    """tenant_id NULL means a network-rule document shared by all tenants."""

    __tablename__ = "policy_documents"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyChunkRow(Base):
    __tablename__ = "policy_chunks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_documents.id", ondelete="CASCADE"))
    tenant_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float))


# --- resolution workflow v2 (migration 0002) ------------------------------------------------------------------


class TenantDisputePolicyRow(Base):
    __tablename__ = "tenant_dispute_policy"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    approval_threshold: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    auto_approve_below: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    partial_pct: Mapped[int] = mapped_column(Integer, default=50)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadRow(Base):
    """One row per graph thread the service starts; the API resolves thread ids only through this table."""

    __tablename__ = "threads"
    __table_args__ = (
        Index(
            "uq_threads_one_active_per_dispute",
            "dispute_id",
            "graph",
            unique=True,
            postgresql_where="status IN ('running', 'awaiting_approval', 'resume_failed')",
        ),
        Index("ix_threads_tenant_dispute", "tenant_id", "dispute_id"),
        Index("ix_threads_graph_version", "graph", "graph_version"),
        CheckConstraint(
            "status IN ('running', 'awaiting_approval', 'done', 'failed', 'resume_failed', 'cancelled')",
            name="ck_threads_status",
        ),
    )
    thread_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    dispute_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disputes.id"))
    graph: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(60))
    started_by: Mapped[str] = mapped_column(String(64))
    graph_version: Mapped[str] = mapped_column(String(40), server_default="2.0.0+s1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_queue", "tenant_id", "status", "priority", "created_at"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'expired', 'auto')", name="ck_approvals_status"),
        CheckConstraint("priority IN ('normal', 'urgent')", name="ck_approvals_priority"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    dispute_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disputes.id"))
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.thread_id"), unique=True)
    requested_by: Mapped[str] = mapped_column(String(64))
    required_role: Mapped[str] = mapped_column(String(20))
    priority: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    summary: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)


class SideEffectRow(Base):
    __tablename__ = "side_effects"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'unknown')", name="ck_side_effects_status"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    dispute_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("disputes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(10))
    external_ref: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OutboxRow(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_tenant_subject", "tenant_id", "subject"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(String(160))
    request_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class ToolCallRow(Base):
    """One row per tool-gateway attempt (allowed or denied). Arguments are stored as a hash, never in clear."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_tenant_at", "tenant_id", "at"),
        CheckConstraint("status IN ('ok', 'error', 'retryable_error', 'denied')", name="ck_tool_calls_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), index=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column()
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    latency_ms: Mapped[int] = mapped_column(Integer)
    args_sha256: Mapped[str] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
