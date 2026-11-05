"""Kill switch for side-effecting tools (APP_TOOLS_ENABLED=false).

Every call returns a permanent failure, so an approved action ends as EXECUTION_FAILED with a clear reason instead
of reaching the payments or network systems. Used until the MCP-backed gateway is enabled, and in incidents."""

from __future__ import annotations

import uuid

from app.application.ports import ToolResult


class DisabledToolGateway:
    async def call(
        self,
        tool: str,
        args: dict[str, object],
        *,
        tenant_id: str,
        idempotency_key: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolResult:
        return ToolResult(ok=False, data={}, retryable=False, error=f"tools disabled (APP_TOOLS_ENABLED=false): {tool}")
