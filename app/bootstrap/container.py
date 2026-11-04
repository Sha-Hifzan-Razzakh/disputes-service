"""Composition root: builds every process-wide resource once and hands out request-scoped services.

Lives on `app.state.container` (no module global). Shutdown closes everything it opened, in reverse order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.case import CaseService
from app.application.ports import Ledger, RecommendationDrafter, ResolutionDrafter, ToolGateway
from app.application.services import DisputeService, PolicyService, SystemClock
from app.bootstrap.settings import Settings
from app.infrastructure.db.session import make_engine, make_sessionmaker
from app.infrastructure.drafting import AnthropicDrafter, RuleBasedDrafter, RuleBasedResolutionDrafter
from app.infrastructure.repositories import SqlDisputeRepository, SqlEvidenceRepository, SqlLedger
from app.infrastructure.retrieval import HashingEmbedder, SqlPolicyStore
from app.infrastructure.tools.disabled import DisabledToolGateway
from app.infrastructure.workflow_store import (
    SqlApprovalRepository,
    SqlAuditLog,
    SqlCaseReader,
    SqlDisputeCommands,
    SqlOutbox,
    SqlSideEffectLedger,
    SqlTenantPolicyRepository,
    SqlThreadRegistry,
)
from app.orchestration.context import Ctx, GraphLimits, GraphServices
from app.orchestration.runners import AssistRunner, ResolutionRunner, RunLimits


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    checkpoint_pool: AsyncConnectionPool[Any]
    http: httpx.AsyncClient  # shared outbound client; the tool gateway's MCP transport uses it (M3)
    policy_store: SqlPolicyStore
    drafter: RecommendationDrafter
    graph_services: GraphServices
    limits: RunLimits
    assist_graph: Any = None  # compiled in lifespan, after the checkpointer exists
    resolution_graph: Any = None
    _closers: list[Any] = field(default_factory=list, repr=False)

    # --- request-scoped factories (one DB session per request; the API never names an adapter)

    def dispute_service(self, s: AsyncSession) -> DisputeService:
        return DisputeService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s), SystemClock())

    def case_service(self, s: AsyncSession) -> CaseService:
        return CaseService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s))

    def ledger(self, s: AsyncSession) -> Ledger:
        return SqlLedger(s)

    def policy_service(self) -> PolicyService:
        return PolicyService(self.policy_store)

    def assist_runner(self) -> AssistRunner:
        if self.assist_graph is None:
            raise RuntimeError("assist graph not compiled; is the lifespan running?")
        return AssistRunner(self.assist_graph, self.limits)

    def resolution_runner(self) -> ResolutionRunner:
        if self.resolution_graph is None:
            raise RuntimeError("resolution graph not compiled; is the lifespan running?")
        return ResolutionRunner(self.resolution_graph, self.limits)

    def graph_context(self, *, tenant_id: str, user_id: str, role: str, request_id: str) -> Ctx:
        return Ctx(tenant_id=tenant_id, user_id=user_id, role=role, request_id=request_id, services=self.graph_services)


def build_drafter(s: Settings) -> RecommendationDrafter:
    """Model client built once, with timeout, retries and a client-side rate limiter (T07.5)."""
    if s.model_provider != "anthropic" or s.anthropic_api_key is None:
        return RuleBasedDrafter()
    from langchain_anthropic import ChatAnthropic
    from langchain_core.rate_limiters import InMemoryRateLimiter

    limiter = InMemoryRateLimiter(requests_per_second=s.model_requests_per_second, max_bucket_size=s.model_max_burst)
    llm = ChatAnthropic(
        model=s.model_name,  # type: ignore[call-arg]
        api_key=s.anthropic_api_key,
        timeout=s.model_timeout_s,
        max_retries=s.model_max_retries,
        rate_limiter=limiter,
    )
    return AnthropicDrafter(llm)


def build_resolution_drafter(s: Settings) -> ResolutionDrafter:
    # The LLM resolution drafter arrives with the eval baseline (M4); until then drafts are rule-based in every env.
    return RuleBasedResolutionDrafter()


def build_tool_gateway(s: Settings) -> ToolGateway:
    return DisabledToolGateway()  # M3 wires the MCP-backed gateway when APP_TOOLS_ENABLED=true


async def build_container(s: Settings) -> Container:
    engine = make_engine(s.database_url.get_secret_value(), pool_size=s.db_pool_size)
    sessions = make_sessionmaker(engine)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=s.checkpoint_dsn,
        min_size=1,
        max_size=s.checkpoint_pool_max,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    await pool.open()
    http = httpx.AsyncClient(
        timeout=httpx.Timeout(s.http_timeout_s, connect=min(3.0, s.http_timeout_s)),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    drafter = build_drafter(s)
    store = SqlPolicyStore(sessions, HashingEmbedder(s.embedding_dim))
    clock = SystemClock()
    services = GraphServices(
        clock=clock,
        policy_store=store,
        drafter=drafter,
        cases=SqlCaseReader(sessions),
        tenant_policies=SqlTenantPolicyRepository(sessions),
        resolution_drafter=build_resolution_drafter(s),
        disputes=SqlDisputeCommands(sessions, clock),
        threads=SqlThreadRegistry(sessions),
        approvals=SqlApprovalRepository(sessions),
        side_effects=SqlSideEffectLedger(sessions),
        tools=build_tool_gateway(s),
        audit=SqlAuditLog(sessions),
        outbox=SqlOutbox(sessions),
        limits=GraphLimits(
            retrieval_top_k=s.retrieval_top_k,
            min_evidence_score=s.min_evidence_score,
            max_retrieval_attempts=s.max_retrieval_attempts,
            max_refine_attempts=s.max_refine_attempts,
            approval_ttl=timedelta(hours=s.approval_ttl_hours),
            urgent_window=timedelta(hours=s.urgent_window_hours),
            approval_deadline_margin=timedelta(hours=s.approval_deadline_margin_hours),
        ),
    )
    c = Container(
        settings=s,
        engine=engine,
        sessions=sessions,
        checkpoint_pool=pool,
        http=http,
        policy_store=store,
        drafter=drafter,
        graph_services=services,
        limits=RunLimits(recursion_limit=s.recursion_limit, max_concurrency=s.max_concurrency),
    )
    c._closers = [engine.dispose, pool.close, http.aclose]
    return c


async def close_container(c: Container) -> None:
    for close in reversed(c._closers):
        await close()
