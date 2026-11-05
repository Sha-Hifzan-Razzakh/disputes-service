"""One long-lived MCP client session per server (T12.4), opened in the lifespan and reconnected once on a transport
failure. The session factory is injected: streamable HTTP with a per-server bearer token in deployments, an
in-memory server in tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import anyio
from mcp.client.client import Client


class ToolTransportError(Exception):
    """The call may or may not have reached the server (connect, timeout, dropped stream): retryable."""


class McpConnection:
    """One MCP session per server, owned by a dedicated task (review R11).

    The session's context manager is entered and exited in that task only, so closing from a request, graph or
    shutdown task never crosses an anyio cancel scope (which leaked the transport and HTTP client before). Calls
    from any task use the shared session. `generation` increases on every successful connect, so the gateway can
    re-check schema pins after each reconnect (review R10). Calls never connect implicitly: `start()` does.
    """

    def __init__(self, name: str, factory: Callable[[], AbstractAsyncContextManager[Client]], timeout_s: float) -> None:
        self.name = name
        self._factory = factory
        self._timeout = timeout_s
        self._client: Client | None = None
        self._owner: asyncio.Task[None] | None = None
        self._closing: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self.generation = 0
        self.schemas: dict[str, dict[str, Any]] = {}

    @property
    def started(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        """Open the session and list tools (idempotent, one opener at a time). Any failure surfaces as
        ToolTransportError (retryable), so a provider outage degrades execution instead of crashing the caller."""
        async with self._lock:
            if self._client is not None:
                return
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._closing = asyncio.Event()
            self._owner = asyncio.create_task(self._own(ready, self._closing), name=f"mcp-session:{self.name}")
            try:
                await ready
            except asyncio.CancelledError:
                await self._stop_owner()
                raise
            except BaseException as e:
                await self._stop_owner()
                raise ToolTransportError(f"{self.name}: cannot connect ({type(e).__name__})") from e

    async def _own(self, ready: asyncio.Future[None], closing: asyncio.Event) -> None:
        try:
            async with self._factory() as client:
                with anyio.fail_after(self._timeout):
                    listed = await client.list_tools()
                self.schemas = {t.name: dict(t.input_schema) for t in listed.tools}
                self._client = client
                self.generation += 1
                ready.set_result(None)
                await closing.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            self._client = None

    async def _stop_owner(self) -> None:
        owner, closing = self._owner, self._closing
        self._owner = None
        if closing is not None:
            closing.set()
        if owner is not None:
            with contextlib.suppress(BaseException):  # closing a broken session must not mask the caller's error
                await asyncio.wait_for(owner, timeout=self._timeout)
        self._client = None

    async def close(self) -> None:
        async with self._lock:
            await self._stop_owner()

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[bool, Any]:
        """Returns (is_error, payload). payload is the parsed JSON result, or the error text when is_error."""
        client = self._client
        if client is None:
            raise ToolTransportError(f"{self.name}: not connected")
        generation = self.generation
        try:
            with anyio.fail_after(self._timeout):
                res = await client.call_tool(tool, args)
        except TimeoutError as e:
            await self._reset(generation)
            raise ToolTransportError(f"{self.name}.{tool} timed out after {self._timeout:g}s") from e
        except Exception as e:  # transport-level failures surface as many types (ExceptionGroup, httpx2 errors)
            await self._reset(generation)
            raise ToolTransportError(f"{self.name}.{tool} transport error: {type(e).__name__}") from e
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:
            return True, text
        if res.structured_content is not None:
            return False, res.structured_content
        try:
            return False, json.loads(text)
        except json.JSONDecodeError:
            # the call succeeded but the answer is unreadable: NOT an error code (that would read as a clean
            # business failure); the gateway treats a non-dict success as malformed → unknown for side effects
            return False, f"not JSON: {text[:80]!r}"

    async def _reset(self, generation: int) -> None:
        """Drop the session after a transport failure, unless another caller already replaced it."""
        async with self._lock:
            if self.generation == generation:
                await self._stop_owner()


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
