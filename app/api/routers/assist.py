from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Principal, assist_service, require_role
from app.api.schemas import AssistIn, AssistOut
from app.application.assist import AssistService

router = APIRouter(prefix="/v1/disputes/{dispute_id}/assist", tags=["assist"])


@router.post("", response_model=AssistOut)
async def assist(
    dispute_id: uuid.UUID,
    body: AssistIn,
    p: Annotated[Principal, Depends(require_role("analyst"))],
    svc: Annotated[AssistService, Depends(assist_service)],
) -> AssistOut:
    out = await svc.ask(tenant_id=p.tenant_id, dispute_id=dispute_id, question=body.question)
    return AssistOut(**out)
