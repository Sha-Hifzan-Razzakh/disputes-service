"""v1 assist graph through the API: recommendation logic and per-dispute thread history."""

from __future__ import annotations

from collections.abc import Callable

import httpx

Auth = Callable[..., dict[str, str]]


async def _dispute_for(client: httpx.AsyncClient, auth: Auth, ref: str, tenant: str = "northwind") -> str:
    items = (await client.get("/v1/disputes?limit=100", headers=auth(tenant=tenant))).json()["items"]
    return next(i["id"] for i in items if i["transaction_ref"] == ref)


async def test_strong_3ds_fraud_is_contested(client: httpx.AsyncClient, auth: Auth) -> None:
    did = await _dispute_for(client, auth, "NW-1001")
    r = await client.post(f"/v1/disputes/{did}/assist", json={"question": "Can we fight this?"}, headers=auth())
    assert r.status_code == 200
    assert r.json()["suggested_resolution"] == "CONTEST" and r.json()["citations"]


async def test_history_accumulates_per_dispute_thread(client: httpx.AsyncClient, auth: Auth) -> None:
    did = await _dispute_for(client, auth, "NW-1003")
    first = await client.post(f"/v1/disputes/{did}/assist", json={"question": "Was it delivered?"}, headers=auth())
    second = await client.post(f"/v1/disputes/{did}/assist", json={"question": "What should we do?"}, headers=auth())
    assert first.json()["suggested_resolution"] == "ACCEPT"
    assert second.json()["turns"] == first.json()["turns"] + 1


async def test_assist_cross_tenant_is_404(client: httpx.AsyncClient, auth: Auth) -> None:
    did = await _dispute_for(client, auth, "NW-1001")
    r = await client.post(f"/v1/disputes/{did}/assist", json={"question": "hello?"}, headers=auth(tenant="aurora"))
    assert r.status_code == 404
