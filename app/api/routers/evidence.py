from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import Principal, dispute_service, require_role
from app.api.schemas import EvidenceIn, EvidenceOut
from app.application.services import DisputeService

router = APIRouter(prefix="/v1/disputes/{dispute_id}/evidence", tags=["evidence"])
Analyst = Annotated[Principal, Depends(require_role("analyst"))]
Svc = Annotated[DisputeService, Depends(dispute_service)]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=EvidenceOut)
async def add_evidence(dispute_id: uuid.UUID, body: EvidenceIn, p: Analyst, svc: Svc) -> EvidenceOut:
    item = await svc.add_evidence(
        tenant_id=p.tenant_id,
        dispute_id=dispute_id,
        actor=p.user_id,
        kind=body.kind,
        description=body.description,
        uri=body.uri,
    )
    return EvidenceOut.of(item)


@router.get("", response_model=list[EvidenceOut])
async def list_evidence(dispute_id: uuid.UUID, p: Analyst, svc: Svc) -> list[EvidenceOut]:
    return [EvidenceOut.of(e) for e in await svc.list_evidence(p.tenant_id, dispute_id)]
