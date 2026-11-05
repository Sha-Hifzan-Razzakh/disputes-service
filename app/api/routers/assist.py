from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import (
    AppContainer,
    Principal,
    assist_runner,
    bounded_run,
    case_service,
    graph_context,
    require_role,
)
from app.api.schemas import AssistIn, AssistOut
from app.application.case import CaseService
from app.orchestration.context import Ctx
from app.orchestration.runners import AssistRunner

router = APIRouter(prefix="/v1/disputes/{dispute_id}/assist", tags=["assist"])


@router.post("", response_model=AssistOut, deprecated=True)
async def assist(
    dispute_id: uuid.UUID,
    body: AssistIn,
    response: Response,
    p: Annotated[Principal, Depends(require_role("analyst"))],
    cases: Annotated[CaseService, Depends(case_service)],
    runner: Annotated[AssistRunner, Depends(assist_runner)],
    ctx: Annotated[Ctx, Depends(graph_context)],
    c: AppContainer,
) -> AssistOut:
    case = await cases.snapshot(p.tenant_id, dispute_id)
    out = await bounded_run(runner.ask(case=case, question=body.question, ctx=ctx), c)
    # RFC 9745 Deprecation + RFC 8594 Sunset; body contract unchanged (spec M8).
    response.headers["Deprecation"] = "@1793491200"  # 2026-11-01T00:00:00Z
    response.headers["Sunset"] = c.settings.v1_assist_sunset
    response.headers["Link"] = f'</v2/disputes/{dispute_id}/resolutions>; rel="successor-version"'
    return AssistOut(**out)
