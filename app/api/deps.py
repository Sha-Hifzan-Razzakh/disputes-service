"""Request-scoped dependencies: principal from JWT, DB session, services and graph context.

Adapters are never named here: services come from the container's factories (`lint-imports` enforces it).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.case import CaseService
from app.application.errors import TooManyRuns
from app.application.ports import Ledger
from app.application.services import DisputeService, PolicyService
from app.bootstrap.container import Container
from app.domain.roles import ROLE_RANK
from app.orchestration.context import Ctx
from app.orchestration.runners import AssistRunner, ResolutionRunner

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


def get_container_dep(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


AppContainer = Annotated[Container, Depends(get_container_dep)]


def current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], c: AppContainer
) -> Principal:
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(
            creds.credentials,
            c.settings.jwt_secret.get_secret_value(),
            algorithms=[c.settings.jwt_algorithm],
            options={"require": ["sub", "tenant", "role"]},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}") from None
    if claims["role"] not in ROLE_RANK:
        raise HTTPException(403, "unknown role")
    return Principal(user_id=claims["sub"], tenant_id=claims["tenant"], role=claims["role"])


def require_role(minimum: str) -> Callable[[Principal], Principal]:
    def dep(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if ROLE_RANK[p.role] < ROLE_RANK[minimum]:
            raise HTTPException(403, f"requires role {minimum}")
        return p

    return dep


async def db_session(c: AppContainer) -> AsyncIterator[AsyncSession]:
    async with c.sessions() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


# scope="function": commit BEFORE the response is sent. With the default ("request") the commit ran after the response,
# so a client's next request — on another worker — could read the old version and get a false 409 (load test, M4).
Session = Annotated[AsyncSession, Depends(db_session, scope="function")]


def dispute_service(s: Session, c: AppContainer) -> DisputeService:
    return c.dispute_service(s)


def case_service(s: Session, c: AppContainer) -> CaseService:
    return c.case_service(s)


def ledger(s: Session, c: AppContainer) -> Ledger:
    return c.ledger(s)


def policy_service(c: AppContainer) -> PolicyService:
    return c.policy_service()


def assist_runner(c: AppContainer) -> AssistRunner:
    return c.assist_runner()


def resolution_runner(c: AppContainer) -> ResolutionRunner:
    return c.resolution_runner()


def graph_context(request: Request, c: AppContainer, p: Annotated[Principal, Depends(current_principal)]) -> Ctx:
    """Ctx for every graph invocation (T07.8): identity from the JWT, request id from the middleware."""
    return c.graph_context(tenant_id=p.tenant_id, user_id=p.user_id, role=p.role, request_id=request.state.request_id)


async def bounded_run[T](run: Coroutine[Any, Any, T], c: Container) -> T:
    """Admission control (T16.13) then the API-level run timeout (T07.9). A run that cannot get a slot within
    APP_ADMISSION_WAIT_S is refused with 429 before touching the graph; a timed-out run keeps its checkpoint."""
    slots = c.run_slots
    if slots is not None:
        try:
            await asyncio.wait_for(slots.acquire(), timeout=c.settings.admission_wait_s)
        except TimeoutError:
            run.close()  # never started
            raise TooManyRuns(f"{c.settings.max_concurrent_runs} runs in progress; retry shortly") from None
    try:
        async with asyncio.timeout(c.settings.run_timeout_s):
            return await run
    except TimeoutError:
        raise HTTPException(504, f"run exceeded {c.settings.run_timeout_s:g}s") from None
    finally:
        if slots is not None:
            slots.release()
