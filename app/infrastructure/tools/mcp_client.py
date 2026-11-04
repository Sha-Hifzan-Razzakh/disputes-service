"""One long-lived MCP client session per server (T12.4), opened in the lifespan and reconnected once on a transport
failure. The session factory is injected: streamable HTTP with a per-server bearer token in deployments, an
in-memory server in tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

import anyio
from mcp.client.client import Client


class ToolTransportError(Exception):
    """The call may or may not have reached the server (connect, timeout, dropped stream): retryable."""


class McpConnection:
    def __init__(self, name: str, factory: Callable[[], AbstractAsyncContextManager[Client]], timeout_s: float) -> None:
        self.name = name
        self._factory = factory
        self._timeout = timeout_s
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self.schemas: dict[str, dict[str, Any]] = {}

    @property
    def started(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        """Open the session and list tools. Any failure closes what was opened and surfaces as ToolTransportError
        (retryable), so a provider outage degrades execution instead of crashing the caller."""
        stack = AsyncExitStack()
        try:
            client = await stack.enter_async_context(self._factory())
            with anyio.fail_after(self._timeout):
                listed = await client.list_tools()
        except BaseException as e:
            with contextlib.suppress(BaseException):
                await stack.aclose()
            if isinstance(e, asyncio.CancelledError):
                raise
            raise ToolTransportError(f"{self.name}: cannot connect ({type(e).__name__})") from e
        self._stack, self._client = stack, client
        self.schemas = {t.name: dict(t.input_schema) for t in listed.tools}

    async def close(self) -> None:
        if self._stack is not None:
            stack, self._stack, self._client = self._stack, None, None
            with contextlib.suppress(BaseException):  # closing a broken session must not mask the caller's error
                await stack.aclose()

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[bool, Any]:
        """Returns (is_error, payload). payload is the parsed JSON result, or the error text when is_error."""
        if self._client is None:
            await self.start()
        assert self._client is not None
        try:
            with anyio.fail_after(self._timeout):
                res = await self._client.call_tool(tool, args)
        except TimeoutError as e:
            await self._reset()
            raise ToolTransportError(f"{self.name}.{tool} timed out after {self._timeout:g}s") from e
        except Exception as e:  # transport-level failures surface as many types (ExceptionGroup, httpx2 errors)
            await self._reset()
            raise ToolTransportError(f"{self.name}.{tool} transport error: {type(e).__name__}") from e
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:
            return True, text
        if res.structured_content is not None:
            return False, res.structured_content
        try:
            return False, json.loads(text)
        except json.JSONDecodeError:
            return True, f"malformed_output: not JSON ({text[:80]!r})"

    async def _reset(self) -> None:
        await self.close()


def http_session_factory(url: str, token: str, timeout_s: float) -> Callable[[], AbstractAsyncContextManager[Client]]:
    """Streamable-HTTP session with this server's own bearer token and timeouts (per-server auth, T12.4)."""
    from contextlib import asynccontextmanager

    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    @asynccontextmanager
    async def session() -> Any:
        hc = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(timeout_s, connect=min(3.0, timeout_s))
        )
        try:
            async with Client(streamable_http_client(url, http_client=hc)) as client:
                yield client
        finally:
            await hc.aclose()

    return session
