"""Uniform error envelope: {"error": {"code", "message", "request_id"}}."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.application.errors import (
    ApprovalChanged,
    ApprovalDecided,
    ApprovalExpired,
    Forbidden,
    ResolutionInProgress,
    RunAborted,
)
from app.domain.errors import DomainError, InvalidTransition, NotFound, VersionConflict


def _envelope(request: Request, status: int, code: str, message: str) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse({"error": {"code": code, "message": message, "request_id": rid}}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFound)
    async def _nf(req: Request, e: NotFound) -> JSONResponse:
        return _envelope(req, 404, "not_found", str(e))

    @app.exception_handler(InvalidTransition)
    async def _it(req: Request, e: InvalidTransition) -> JSONResponse:
        return _envelope(req, 409, "invalid_transition", str(e))

    @app.exception_handler(VersionConflict)
    async def _vc(req: Request, e: VersionConflict) -> JSONResponse:
        return _envelope(req, 409, "version_conflict", str(e))

    @app.exception_handler(ResolutionInProgress)
    async def _rip(req: Request, e: ResolutionInProgress) -> JSONResponse:
        return _envelope(req, 409, "resolution_in_progress", str(e))

    @app.exception_handler(ApprovalDecided)
    async def _ad(req: Request, e: ApprovalDecided) -> JSONResponse:
        return _envelope(req, 409, "approval_decided", str(e))

    @app.exception_handler(ApprovalExpired)
    async def _ae(req: Request, e: ApprovalExpired) -> JSONResponse:
        return _envelope(req, 410, "approval_expired", str(e))

    @app.exception_handler(Forbidden)
    async def _fb(req: Request, e: Forbidden) -> JSONResponse:
        return _envelope(req, 403, "forbidden", str(e))

    @app.exception_handler(ApprovalChanged)
    async def _ac(req: Request, e: ApprovalChanged) -> JSONResponse:
        return _envelope(req, 409, "approval_changed", str(e))

    @app.exception_handler(RunAborted)
    async def _ra(req: Request, e: RunAborted) -> JSONResponse:
        structlog.get_logger().error("run_aborted", thread_id=e.thread_id, reason=e.reason, alert=True)
        return _envelope(req, 500, "run_aborted", f"run {e.thread_id} stopped: {e.reason}")

    @app.exception_handler(DomainError)
    async def _de(req: Request, e: DomainError) -> JSONResponse:
        return _envelope(req, 422, "rule_violation", str(e))

    @app.exception_handler(HTTPException)
    async def _http(req: Request, e: HTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 504: "run_timeout"}
        return _envelope(req, e.status_code, codes.get(e.status_code, "http_error"), str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _val(req: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []))
        return _envelope(req, 422, "invalid_request", f"{loc}: {first.get('msg', 'invalid')}")
