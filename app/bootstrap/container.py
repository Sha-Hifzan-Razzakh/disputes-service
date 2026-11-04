"""Composition root: builds every process-wide resource once and hands out request-scoped services.

Lives on `app.state.container` (no module global). Shutdown closes everything it opened, in reverse order.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.application.approvals import ApprovalService
from app.application.case import CaseService
from app.application.ports import Ledger, RecommendationDrafter, ResolutionDrafter, ToolGateway
from app.application.services import DisputeService, PolicyService, SystemClock
from app.bootstrap.settings import Settings
from app.infrastructure.db.session import make_engine, make_sessionmaker
from app.infrastructure.drafting import (
    AnthropicDrafter,
    LlmResolutionDrafter,
    RuleBasedDrafter,
    RuleBasedResolutionDrafter,
)
from app.infrastructure.repositories import SqlDisputeRepository, SqlEvidenceRepository, SqlLedger
from app.infrastructure.retrieval import HashingEmbedder, SqlPolicyStore
from app.infrastructure.tools.disabled import DisabledToolGateway
from app.infrastructure.tools.gateway import McpToolGateway, TokenBucket
from app.infrastructure.tools.mcp_client import McpConnection, http_session_factory
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
    policy_store: SqlPolicyStore
    drafter: RecommendationDrafter
    graph_services: GraphServices
    limits: RunLimits
    assist_graph: Any = None  # compiled in lifespan, after the checkpointer exists
    resolution_graph: Any = None
    mcp: dict[str, Any] = field(default_factory=dict)  # server name -> McpConnection (closed on shutdown)
    telemetry: dict[str, Any] = field(default_factory=dict)
    run_slots: asyncio.Semaphore | None = None  # admission control: concurrent graph runs in this process
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

    def approval_service(self) -> ApprovalService:
        gs = self.graph_services
        return ApprovalService(gs.approvals, gs.audit, gs.clock)

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
    """Rules by default; the LLM drafter when APP_MODEL_PROVIDER=anthropic (same client settings as T07.5)."""
    if s.model_provider != "anthropic" or s.anthropic_api_key is None:
        return RuleBasedResolutionDrafter()
    from langchain_anthropic import ChatAnthropic
    from langchain_core.rate_limiters import InMemoryRateLimiter

    llm = ChatAnthropic(
        model=s.model_name,  # type: ignore[call-arg]
        api_key=s.anthropic_api_key,
        timeout=s.model_timeout_s,
        max_retries=s.model_max_retries,
        rate_limiter=InMemoryRateLimiter(
            requests_per_second=s.model_requests_per_second, max_bucket_size=s.model_max_burst
        ),
        temperature=0,
    )

    async def complete(prompt: str) -> str:
        msg = await llm.ainvoke(prompt)
        return msg.content if isinstance(msg.content, str) else "".join(str(p) for p in msg.content)

    return LlmResolutionDrafter(complete)


def build_tool_gateway(s: Settings) -> ToolGateway:
    """Kill switch until `connect_tools` replaces it in the lifespan (only when APP_TOOLS_ENABLED=true)."""
    return DisabledToolGateway()


async def connect_tools(c: Container, factories: dict[str, Any] | None = None) -> ToolGateway:
    """Open one MCP session per server, compare live tool schemas with the reviewed pins, build the gateway.
    A pin mismatch disables only the affected tools; an unreachable server is logged and retried on first use, so
    a provider outage never blocks the service from starting (starts and approvals keep working)."""
    s = c.settings
    if factories is None:
        assert s.mcp_payments_token is not None and s.mcp_network_token is not None
        factories = {
            "payments": http_session_factory(
                s.mcp_payments_url, s.mcp_payments_token.get_secret_value(), s.tool_timeout_s
            ),
            "network-gateway": http_session_factory(
                s.mcp_network_url, s.mcp_network_token.get_secret_value(), s.tool_timeout_s
            ),
        }
    for name, factory in factories.items():
        c.mcp[name] = McpConnection(name, factory, s.tool_timeout_s)
    gateway = McpToolGateway(
        connections=c.mcp,
        approvals=c.graph_services.approvals,
        sessions=c.sessions,
        limiter=TokenBucket(rate=s.tool_rate_per_s, burst=s.tool_burst),
        max_attempts=s.tool_max_attempts,
    )
    for name in c.mcp:  # load tools and check pins now (T12.4); an unreachable server is retried on first use
        if not await gateway.verify(name):
            structlog.get_logger().error("tool_server_unavailable", server=name, alert=True)
    return gateway


async def build_container(s: Settings) -> Container:
    engine = make_engine(s.database_url.get_secret_value(), pool_size=s.db_pool_size, max_overflow=s.db_max_overflow)
    sessions = make_sessionmaker(engine)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=s.checkpoint_dsn,
        min_size=1,
        max_size=s.checkpoint_pool_max,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
    )
    await pool.open()
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
        policy_store=store,
        drafter=drafter,
        graph_services=services,
        limits=RunLimits(recursion_limit=s.recursion_limit, max_concurrency=s.max_concurrency),
    )
    c.run_slots = asyncio.Semaphore(s.max_concurrent_runs)
    c._closers = [engine.dispose, pool.close]
    return c


async def close_container(c: Container) -> None:
    for conn in c.mcp.values():
        await conn.close()
    for close in reversed(c._closers):
        await close()
