"""Uniform error envelope: {"error": {"code", "message", "request_id"}}."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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

    @app.exception_handler(DomainError)
    async def _de(req: Request, e: DomainError) -> JSONResponse:
        return _envelope(req, 422, "rule_violation", str(e))

    @app.exception_handler(HTTPException)
    async def _http(req: Request, e: HTTPException) -> JSONResponse:
        codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}
        return _envelope(req, e.status_code, codes.get(e.status_code, "http_error"), str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _val(req: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []))
        return _envelope(req, 422, "invalid_request", f"{loc}: {first.get('msg', 'invalid')}")
