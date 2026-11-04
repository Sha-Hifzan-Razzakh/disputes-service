"""Test doubles that sit behind the graph's ports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.application.ports import ToolResult, TransientError


@dataclass
class FakeToolGateway:
    """Behaves like the payments / network MCP servers: dedupes on the idempotency key."""

    transient_failures: int = 0
    permanent_error: str | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    effects: dict[str, str] = field(default_factory=dict)  # idempotency key -> external ref

    async def call(
        self,
        tool: str,
        args: dict[str, object],
        *,
        tenant_id: str,
        idempotency_key: str | None = None,
        approval_id: uuid.UUID | None = None,
    ) -> ToolResult:
        self.calls.append({"tool": tool, "args": args, "tenant_id": tenant_id, "key": idempotency_key})
        if self.permanent_error:
            return ToolResult(ok=False, data={}, retryable=False, error=self.permanent_error)
        if self.transient_failures:
            self.transient_failures -= 1
            return ToolResult(ok=False, data={"attempts": 3}, retryable=True, error="timeout")
        assert idempotency_key
        ref = self.effects.setdefault(idempotency_key, f"ext_{len(self.effects) + 1}")
        return ToolResult(ok=True, data={"external_ref": ref, "attempts": 1})


@dataclass
class FailOnce:
    """Wraps an adapter method: raises `exc` the first `times` calls whose kwargs match `when`, then delegates."""

    inner: Any
    method: str
    exc: Exception
    when: dict[str, Any] = field(default_factory=dict)
    times: int = 1

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.inner, name)
        if name != self.method:
            return target

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if self.times and all(kwargs.get(k) == v for k, v in self.when.items()):
                self.times -= 1
                raise self.exc
            return await target(*args, **kwargs)

        return wrapped


TRANSIENT = TransientError("simulated connection drop")
