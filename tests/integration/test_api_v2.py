"""API end to end (T15.8) for paths not covered elsewhere: v2 timeout, partial refund, MCP over real HTTP."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import socket
import sys
from typing import Any

import pytest

from tests.graph.test_resolution_graph import dispute, sql, start


async def test_run_over_the_api_timeout_returns_504_and_stays_resumable(
    client, auth, app, mcp_tools, monkeypatch
) -> None:
    c = app.state.container
    real = c.graph_services.resolution_drafter

    class Slow:
        async def draft(self, **kw: Any) -> Any:
            await asyncio.sleep(1.0)
            return await real.draft(**kw)

    services = c.graph_services
    c.graph_services = dataclasses.replace(services, resolution_drafter=Slow())
    monkeypatch.setattr(c.settings, "run_timeout_s", 0.3)
    try:
        did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
        r = await start(client, auth, did)
    finally:
        c.graph_services = services
    assert r.status_code == 504 and r.json()["error"]["code"] == "run_timeout"
    tid = sql("SELECT thread_id FROM threads WHERE dispute_id = %s", did)[0][0]
    view = (await client.get(f"/v2/disputes/{did}/resolutions/{tid}", headers=auth())).json()
    assert view["status"] == "running" and view["next"] == ["draft_resolution"]  # last checkpoint kept
    monkeypatch.setattr(c.settings, "run_timeout_s", 30)
    r = await client.post(f"/admin/threads/{tid}/recover", headers=auth(user="root", role="admin"))
    assert r.status_code == 200 and r.json()["outcome"] == "AWAITING_APPROVAL"


async def test_approved_partial_refund(client, auth, mcp_tools) -> None:
    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")
    r = await client.post(
        f"/v1/disputes/{did}/evidence",
        json={"kind": "product_description", "description": "Listing photo and spec sheet"},
        headers=auth(),
    )
    assert r.status_code == 201
    v = (await start(client, auth, did)).json()
    assert v["draft"]["resolution"] == "PARTIAL" and v["draft"]["amount"] == "37.50"
    ap = (await client.get(f"/v2/approvals/{v['approval']['approval_id']}", headers=auth(user="ben"))).json()
    r = await client.post(
        f"/v2/approvals/{ap['id']}/decision",
        headers=auth(user="ben"),
        json={"decision": "approve", "summary_hash": ap["summary_hash"]},
    )
    assert r.json()["resolution"]["outcome"] == "PARTIALLY_REFUNDED"
    assert mcp_tools["ledger"].transactions[("northwind", "NW-1005")]["refunded"] == 37.5


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def payments_http() -> Any:
    port = _free_port()
    env = os.environ | {"MCP_PAYMENTS_TOKEN": "it-token", "PYTHONPATH": "."}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "mcp_servers.payments:app",
        "--port",
        str(port),
        "--log-level",
        "warning",
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            _, w = await asyncio.open_connection("127.0.0.1", port)
            w.close()
            break
        except OSError:
            await asyncio.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    await proc.wait()


async def test_mcp_server_over_real_http_with_token(payments_http: str) -> None:
    """T15.7: the production connection code against the server process, auth included."""
    from app.infrastructure.tools.mcp_client import McpConnection, ToolTransportError, http_session_factory
    from app.infrastructure.tools.policy import pin_mismatches

    conn = McpConnection("payments", http_session_factory(payments_http, "it-token", 4.0), 4.0)
    await conn.start()
    assert pin_mismatches({"payments": conn.schemas}) == {}
    is_error, out = await conn.call("get_transaction", {"tenant_id": "northwind", "transaction_ref": "NW-1003"})
    assert not is_error and out["amount"] == "89.50"
    await conn.close()
    bad = McpConnection("payments", http_session_factory(payments_http, "wrong", 2.0), 2.0)
    with pytest.raises(ToolTransportError, match="cannot connect"):
        await bad.start()


async def test_admission_control_refuses_with_429_when_full(client, auth, app, tools, monkeypatch) -> None:
    """T16.13: with every run slot taken, a new start is refused quickly with Retry-After, before touching the
    graph, and no thread is registered."""
    c = app.state.container
    monkeypatch.setattr(c.settings, "admission_wait_s", 0.05)
    slots = asyncio.Semaphore(1)
    await slots.acquire()
    monkeypatch.setattr(c, "run_slots", slots)
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    r = await start(client, auth, did)
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_runs" and r.headers["Retry-After"] == "2"
    assert sql("SELECT count(*) FROM threads WHERE dispute_id = %s", did) == [(0,)]
    slots.release()
    assert (await start(client, auth, did)).status_code == 202
