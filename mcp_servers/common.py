"""Shared pieces for the simulated MCP servers: bearer-token auth, business errors, failure injection."""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Error codes the gateway treats as retryable; everything else is permanent.
RETRYABLE = {"temporarily_unavailable", "rate_limited"}


class ToolFailure(ToolError):
    """Raised inside a tool: the MCP result has is_error=True and text '... <code>: <message>' (a deliberate
    ToolError keeps its message; any other exception is reported to the client without it)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


@dataclass
class FailureInjection:
    """Test/chaos hook: fail the next n calls of a tool with a code (e.g. temporarily_unavailable)."""

    pending: dict[str, list[str]] = field(default_factory=dict)
    malformed: dict[str, int] = field(default_factory=dict)

    def malformed_next(self, tool: str, times: int = 1) -> None:
        """The next call does its work but answers without the agreed output keys (chaos: T16.5)."""
        self.malformed[tool] = self.malformed.get(tool, 0) + times

    def take_malformed(self, tool: str) -> bool:
        if self.malformed.get(tool):
            self.malformed[tool] -= 1
            return True
        return False

    def fail_next(self, tool: str, code: str, times: int = 1) -> None:
        self.pending.setdefault(tool, []).extend([code] * times)

    def check(self, tool: str) -> None:
        codes = self.pending.get(tool)
        if codes:
            code = codes.pop(0)
            raise ToolFailure(code, f"injected failure for {tool}")


def transport_security(service: str) -> TransportSecuritySettings:
    """DNS-rebinding protection that accepts the names this server is really reached by: localhost, and its
    service name in Kubernetes / ECS Service Connect ("payments:8101"). The SDK's default (localhost only)
    answered 421 to every in-cluster call (found by the first staging deploy). MCP_ALLOWED_HOSTS adds more."""
    extra = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", f"{service}:*", *extra],
        allowed_origins=[],
    )


def with_bearer_auth(app: Starlette, token_env: str) -> Starlette:
    expected = os.environ.get(token_env, "")

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            got = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not expected or not hmac.compare_digest(got, expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerAuth)
    return app
