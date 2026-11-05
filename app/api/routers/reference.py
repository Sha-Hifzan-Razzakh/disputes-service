"""Transaction lookup, policy ingestion and health endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from app.api.deps import AppContainer, Principal, ledger, policy_service, require_role
from app.api.schemas import PolicyDocIn, PolicyDocOut, TransactionOut
from app.application.ports import Ledger
from app.application.services import PolicyService

router = APIRouter()


@router.get("/v1/transactions/{ref}", response_model=TransactionOut, tags=["transactions"])
async def get_transaction(
    ref: str, p: Annotated[Principal, Depends(require_role("analyst"))], lg: Annotated[Ledger, Depends(ledger)]
) -> TransactionOut:
    return TransactionOut.of(await lg.get_transaction(p.tenant_id, ref))


@router.post(
    "/v1/policies/documents", status_code=status.HTTP_201_CREATED, response_model=PolicyDocOut, tags=["policies"]
)
async def ingest_policy(
    body: PolicyDocIn,
    p: Annotated[Principal, Depends(require_role("ops_lead"))],
    svc: Annotated[PolicyService, Depends(policy_service)],
) -> PolicyDocOut:
    if body.scope == "network" and p.role != "admin":
        raise HTTPException(403, "network-scope documents require admin")
    tenant = None if body.scope == "network" else p.tenant_id
    n = await svc.ingest(tenant_id=tenant, title=body.title, source=body.source, text=body.text)
    return PolicyDocOut(chunks=n, scope=body.scope)


@router.get("/healthz", tags=["health"])
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@router.get("/readyz", tags=["health"])
async def readyz(c: AppContainer) -> dict[str, bool]:
    async with c.engine.connect() as conn:
        await conn.execute(text("select 1"))
    async with c.checkpoint_pool.connection() as conn:
        await conn.execute("select 1")
    if c.assist_graph is None:
        raise HTTPException(503, "graph not compiled")
    return {"ok": True}
