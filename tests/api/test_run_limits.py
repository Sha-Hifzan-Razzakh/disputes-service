"""Stage 7 runtime: context reaches nodes, run timeout maps to 504, adapters are not rebuilt per request."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

Auth = Callable[..., dict[str, str]]


async def _dispute(client: httpx.AsyncClient, auth: Auth, ref: str) -> str:
    items = (await client.get("/v1/disputes?limit=100", headers=auth())).json()["items"]
    return next(i["id"] for i in items if i["transaction_ref"] == ref)


async def test_nodes_read_tenant_from_context(
    client: httpx.AsyncClient, auth: Auth, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = app.state.container.graph_services.policy_store
    seen: list[str] = []
    real = store.search

    async def spy(*, tenant_id: str, query: str, k: int) -> Any:
        seen.append(tenant_id)
        return await real(tenant_id=tenant_id, query=query, k=k)

    monkeypatch.setattr(store, "search", spy)
    did = await _dispute(client, auth, "NW-1001")
    r = await client.post(
        f"/v1/disputes/{did}/assist", json={"question": "ctx?"}, headers=auth() | {"X-Request-ID": "rid-ctx-1"}
    )
    assert r.status_code == 200 and seen == ["northwind"]


async def test_run_timeout_returns_504_envelope(
    client: httpx.AsyncClient, auth: Auth, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = app.state.container
    drafter = c.graph_services.drafter

    async def slow(**_: Any) -> Any:
        await asyncio.sleep(1)

    monkeypatch.setattr(drafter, "draft", slow)
    monkeypatch.setattr(c.settings, "run_timeout_s", 0.2)
    did = await _dispute(client, auth, "NW-1002")
    r = await client.post(f"/v1/disputes/{did}/assist", json={"question": "slow?"}, headers=auth())
    assert r.status_code == 504
    body = r.json()["error"]
    assert body["code"] == "run_timeout" and body["request_id"]


async def test_graph_compiled_once_and_shared(app: Any) -> None:
    c = app.state.container
    assert c.assist_runner().graph is c.assist_runner().graph is c.assist_graph
