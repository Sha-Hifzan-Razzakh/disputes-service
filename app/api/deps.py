"""Request-scoped dependencies: principal from JWT, DB session, services."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.assist import AssistService
from app.application.services import DisputeService, PolicyService, SystemClock
from app.bootstrap.container import Container
from app.infrastructure.repositories import SqlDisputeRepository, SqlEvidenceRepository, SqlLedger

ROLE_RANK = {"analyst": 1, "ops_lead": 2, "admin": 3}
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str


def get_container_dep(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def current_principal(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    c: Annotated[Container, Depends(get_container_dep)],
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


async def db_session(c: Annotated[Container, Depends(get_container_dep)]) -> AsyncIterator[AsyncSession]:
    async with c.sessions() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


Session = Annotated[AsyncSession, Depends(db_session)]


def dispute_service(s: Session) -> DisputeService:
    return DisputeService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s), SystemClock())


def assist_service(s: Session, c: Annotated[Container, Depends(get_container_dep)]) -> AssistService:
    return AssistService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s), c.assist_graph)


def policy_service(c: Annotated[Container, Depends(get_container_dep)]) -> PolicyService:
    return PolicyService(c.policy_store)
