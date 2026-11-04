from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import Principal, dispute_service, require_role
from app.api.schemas import DisputeActionIn, DisputeOut, DisputePage, OpenDisputeIn, TimelineOut
from app.application.services import DisputeService
from app.domain.money import Money

router = APIRouter(prefix="/v1/disputes", tags=["disputes"])
Analyst = Annotated[Principal, Depends(require_role("analyst"))]
Svc = Annotated[DisputeService, Depends(dispute_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DisputeOut)
async def open_dispute(body: OpenDisputeIn, p: Analyst, svc: Svc) -> DisputeOut:
    d = await svc.open(
        tenant_id=p.tenant_id,
        actor=p.user_id,
        network=body.network,
        reason_code=body.reason_code,
        transaction_ref=body.transaction_ref,
        amount=Money(body.amount, body.currency),
    )
    return DisputeOut.of(d)


@router.get("", response_model=DisputePage)
async def list_disputes(
    p: Analyst,
    svc: Svc,
    status_: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DisputePage:
    items = await svc.page(p.tenant_id, status=status_, limit=limit, offset=offset)
    return DisputePage(items=[DisputeOut.of(d) for d in items], limit=limit, offset=offset)


@router.get("/{dispute_id}", response_model=DisputeOut)
async def get_dispute(dispute_id: uuid.UUID, p: Analyst, svc: Svc) -> DisputeOut:
    return DisputeOut.of(await svc.get(p.tenant_id, dispute_id))


@router.patch("/{dispute_id}", response_model=DisputeOut)
async def act_on_dispute(dispute_id: uuid.UUID, body: DisputeActionIn, p: Analyst, svc: Svc) -> DisputeOut:
    d = await svc.act(
        tenant_id=p.tenant_id,
        dispute_id=dispute_id,
        actor=p.user_id,
        expected_version=body.expected_version,
        action=body.action,
        assign_to=body.assign_to,
        note=body.note,
    )
    return DisputeOut.of(d)


@router.post("/{dispute_id}/withdraw", response_model=DisputeOut)
async def withdraw(
    dispute_id: uuid.UUID, p: Analyst, svc: Svc, expected_version: Annotated[int, Query(ge=1)]
) -> DisputeOut:
    d = await svc.act(
        tenant_id=p.tenant_id,
        dispute_id=dispute_id,
        actor=p.user_id,
        expected_version=expected_version,
        action="withdraw",
    )
    return DisputeOut.of(d)


@router.get("/{dispute_id}/timeline", response_model=list[TimelineOut])
async def timeline(dispute_id: uuid.UUID, p: Analyst, svc: Svc) -> list[TimelineOut]:
    return [TimelineOut.of(t) for t in await svc.timeline(p.tenant_id, dispute_id)]
