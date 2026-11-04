"""ToolGateway (ADR-0004, T12.5-T12.11): the only path from the graph to external systems.

Pipeline per call:
  policy (deny by default, pinned schema) → tenant binding (caller tenant from Ctx, never from args) →
  side-effect guard (approval approved/auto, idempotency key) → per-tenant rate limit → call with timeout,
  retrying transient failures with the same key → output validation and truncation → one tool_calls row per
  attempt (args hashed) → metrics and a span.

Model output never selects a tool here: the graph passes a tool name from a fixed map (resolution.TOOLS).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from opentelemetry import metrics, trace

from app.application.ports import ApprovalRepository, ToolResult
from app.domain.errors import NotFound
from app.infrastructure.db.models import ToolCallRow
from app.infrastructure.db.session import Sessions, transaction
from app.infrastructure.tools.mcp_client import McpConnection, ToolTransportError
from app.infrastructure.tools.policy import POLICY, pin_mismatches

RETRYABLE_CODES = {"temporarily_unavailable", "rate_limited"}
_ERROR_PREFIX = re.compile(r"^Error executing tool \S+: ")
_CRASH = re.compile(r"^Error executing tool \S+$")
MAX_STRING = 2000

_tracer = trace.get_tracer("disputes.tools")
_meter = metrics.get_meter("disputes.tools")
_calls = _meter.create_counter("tool_calls", description="Tool gateway attempts by tool and status")
_latency = _meter.create_histogram("tool_call_duration", unit="ms", description="Tool call latency")


@dataclass
class TokenBucket:
    rate: float
    burst: int
    _state: dict[tuple[str, str], tuple[float, float]] = field(default_factory=dict)

    def allow(self, key: tuple[str, str]) -> bool:
        now = time.monotonic()
        tokens, last = self._state.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1, now)
        return True


def _args_hash(args: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_STRING]
    if isinstance(value, dict):
        return {str(k): _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value[:100]]
    return value


def _bound(
    summary: Mapping[str, Any], dispute_id: uuid.UUID, tool: str, args: Mapping[str, object], key: str
) -> str | None:
    """An approval authorises one call: this dispute, this tool, this idempotency key, this amount and evidence.
    Reusing it with a fresh key, another dispute or another amount is refused (review R4)."""
    if str(args.get("dispute_id")) != str(dispute_id):
        return "approval is for another dispute"
    if summary.get("tool") != tool:
        return "approval does not cover this tool"
    if summary.get("idempotency_key") != key:
        return "approval is bound to another idempotency key"
    if "amount" in args:
        try:
            same = Decimal(str(args["amount"])) == Decimal(str(summary.get("amount")))
        except InvalidOperation:
            same = False
        if not same or args.get("currency") != summary.get("currency"):
            return "amount differs from the approved summary"
    if "evidence_ids" in args and list(args["evidence_ids"]) != list(summary.get("citations") or []):  # type: ignore[call-overload]
        return "evidence differs from the approved summary"
    return None


class McpToolGateway:
    def __init__(
        self,
        *,
        connections: Mapping[str, McpConnection],
        approvals: ApprovalRepository,
        sessions: Sessions,
        limiter: TokenBucket,
        max_attempts: int = 3,
        backoff_s: float = 0.2,
        disabled: Mapping[str, str] | None = None,
    ) -> None:
        self.connections = connections
        self.approvals = approvals
        self.sessions = sessions
        self.limiter = limiter
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.disabled = dict(disabled or {})  # tool -> reason (schema pin mismatch)
        self._verified: set[str] = set()

    async def verify(self, server: str) -> bool:
        """Connect (if needed) and compare the server's live schemas with the pins, once per server. A failed
        connect leaves the server unverified; the next call tries again."""
        if server in self._verified:
            return True
        conn = self.connections[server]
        try:
            if not conn.started:
                await conn.start()
        except ToolTransportError:
            return False
        for tool, reason in pin_mismatches({server: conn.schemas}).items():
            self.disabled[tool] = reason
            structlog.get_logger().error("tool_disabled", tool=tool, reason=reason, alert=True)
        self._verified.add(server)
        return True

    async def call(
        self,
        tool: str,
        args: dict[str, object],
        *,
        tenant_id: str,
        idempotency_key: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolResult:
        with _tracer.start_as_current_span(f"tool {tool}", attributes={"tool": tool, "tenant_id": tenant_id}) as span:
            denied = await self._check(tool, args, tenant_id=tenant_id, key=idempotency_key, approval_id=approval_id)
            if denied:
                await self._log(tool, tenant_id, idempotency_key, approval_id, 0, "denied", 0, args, denied)
                span.set_attribute("tool.status", "denied")
                return ToolResult(ok=False, data={"attempts": 0}, retryable=False, error=f"denied: {denied}")
            if not self.limiter.allow((tenant_id, tool)):
                await self._log(tool, tenant_id, idempotency_key, approval_id, 0, "denied", 0, args, "rate_limited")
                return ToolResult(ok=False, data={"attempts": 0}, retryable=True, error="rate_limited")
            rule = POLICY[tool]
            payload: dict[str, Any] = {**args, "tenant_id": tenant_id}
            if rule.side_effect:
                payload["idempotency_key"] = idempotency_key
            error: str | None = "no attempt made"
            for attempt in range(1, self.max_attempts + 1):
                t0 = time.perf_counter()
                status, data, error = await self._attempt(rule.server, rule.name, payload, rule.output_keys)
                ms = int((time.perf_counter() - t0) * 1000)
                logged = "error" if status == "malformed" else status
                await self._log(tool, tenant_id, idempotency_key, approval_id, attempt, logged, ms, payload, error)
                span.set_attribute("tool.attempts", attempt)
                span.set_attribute("tool.status", status)
                if status == "ok":
                    return ToolResult(ok=True, data={**data, "attempts": attempt})
                if status == "error":
                    return ToolResult(ok=False, data={"attempts": attempt}, retryable=False, error=error)
                if status == "malformed":
                    # the call reached the provider; for a side effect we cannot tell whether it happened, so the
                    # caller must treat it as unknown (reconciliation), not as a clean failure. Retrying won't help.
                    return ToolResult(ok=False, data={"attempts": attempt}, retryable=rule.side_effect, error=error)
                if attempt < self.max_attempts:
                    await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
            return ToolResult(ok=False, data={"attempts": self.max_attempts}, retryable=True, error=error)

    async def _check(
        self, tool: str, args: Mapping[str, object], *, tenant_id: str, key: str | None, approval_id: uuid.UUID | None
    ) -> str | None:
        rule = POLICY.get(tool)
        if rule is None:
            return f"tool {tool!r} is not allowed"
        if rule.server not in self.connections:
            return f"server {rule.server!r} not configured"
        await self.verify(rule.server)  # unreachable → stays unverified; the attempt below reports it as retryable
        if tool in self.disabled:
            return f"tool {tool!r} disabled: {self.disabled[tool]}"
        if "tenant_id" in args and args["tenant_id"] != tenant_id:
            return "tenant mismatch between caller and arguments"
        if not rule.side_effect:
            return None
        if not key:
            return "side-effecting tool needs an idempotency key"
        if approval_id is None:
            return "side-effecting tool needs an approval"
        try:
            ap = await self.approvals.get(tenant_id, approval_id)
        except NotFound:
            return "approval not found for this tenant"
        if ap.status not in ("approved", "auto"):
            return f"approval is {ap.status}"
        return _bound(ap.summary, ap.dispute_id, tool, args, key)

    async def _attempt(
        self, server: str, name: str, payload: dict[str, Any], output_keys: frozenset[str]
    ) -> tuple[str, dict[str, Any], str | None]:
        try:
            is_error, result = await self.connections[server].call(name, payload)
        except ToolTransportError as e:
            return "retryable_error", {}, str(e)
        if is_error:
            if _CRASH.match(str(result)):  # server crashed without a business code: outcome unknown → retry same key
                return "retryable_error", {}, f"server_error: {result}"
            text = _ERROR_PREFIX.sub("", str(result))  # "Error executing tool x: <code>: <message>"
            code = text.split(":", 1)[0].strip()
            return ("retryable_error" if code in RETRYABLE_CODES else "error"), {}, text[:500]
        if not isinstance(result, dict):
            return "malformed", {}, f"malformed_output: {str(result)[:120]}"
        if not output_keys <= set(result):
            return "malformed", {}, f"malformed_output: missing {sorted(output_keys - set(result))}"
        return "ok", _truncate(result), None

    async def _log(
        self,
        tool: str,
        tenant_id: str,
        key: str | None,
        approval_id: uuid.UUID | None,
        attempt: int,
        status: str,
        ms: int,
        args: Mapping[str, object],
        error: str | None,
    ) -> None:
        _calls.add(1, {"tool": tool, "status": status})
        if attempt:
            _latency.record(ms, {"tool": tool})
        from structlog.contextvars import get_contextvars

        async with transaction(self.sessions) as s:
            s.add(
                ToolCallRow(
                    tenant_id=tenant_id,
                    tool=tool,
                    idempotency_key=key,
                    approval_id=approval_id,
                    attempt=attempt,
                    status=status,
                    latency_ms=ms,
                    args_sha256=_args_hash(args),
                    error=error,
                    request_id=get_contextvars().get("request_id"),
                )
            )
