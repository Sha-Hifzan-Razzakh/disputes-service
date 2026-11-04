"""v1 API behaviour against a real Postgres database."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

Auth = Callable[..., dict[str, str]]


async def _open(
    client: httpx.AsyncClient, auth: Auth, ref: str = "NW-1007", amount: str = "100.00", code: str = "13.3"
) -> dict:
    r = await client.post(
        "/v1/disputes",
        json={"network": "VISA", "reason_code": code, "transaction_ref": ref, "amount": amount, "currency": "USD"},
        headers=auth(),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/disputes")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


async def test_open_and_get(client: httpx.AsyncClient, auth: Auth) -> None:
    d = await _open(client, auth)
    assert d["status"] == "OPENED" and d["version"] == 1
    r = await client.get(f"/v1/disputes/{d['id']}", headers=auth())
    assert r.status_code == 200 and r.headers["X-Request-ID"]


async def test_amount_over_transaction_rejected(client: httpx.AsyncClient, auth: Auth) -> None:
    r = await client.post(
        "/v1/disputes",
        json={
            "network": "VISA",
            "reason_code": "13.3",
            "transaction_ref": "NW-1001",
            "amount": "500.00",
            "currency": "USD",
        },
        headers=auth(),
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "rule_violation"


async def test_unknown_reason_code_and_transaction(client: httpx.AsyncClient, auth: Auth) -> None:
    body = {"network": "VISA", "reason_code": "99.9", "transaction_ref": "NW-1001", "amount": "1.00", "currency": "USD"}
    assert (await client.post("/v1/disputes", json=body, headers=auth())).status_code == 404
    body |= {"reason_code": "13.1", "transaction_ref": "NOPE-1"}
    assert (await client.post("/v1/disputes", json=body, headers=auth())).status_code == 404


async def test_tenant_isolation(client: httpx.AsyncClient, auth: Auth) -> None:
    d = await _open(client, auth)
    r = await client.get(f"/v1/disputes/{d['id']}", headers=auth(tenant="aurora"))
    assert r.status_code == 404
    page = (await client.get("/v1/disputes", headers=auth(tenant="aurora"))).json()
    assert all(i["id"] != d["id"] for i in page["items"])


async def test_lifecycle_with_optimistic_concurrency(client: httpx.AsyncClient, auth: Auth) -> None:
    d = await _open(client, auth)
    url = f"/v1/disputes/{d['id']}"
    r = await client.patch(url, json={"action": "assign", "assign_to": "bob", "expected_version": 1}, headers=auth())
    assert r.json()["assigned_to"] == "bob" and r.json()["version"] == 2
    stale = await client.patch(url, json={"action": "start_evidence", "expected_version": 1}, headers=auth())
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"
    bad = await client.patch(url, json={"action": "resolve_accept", "expected_version": 2}, headers=auth())
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "invalid_transition"
    for i, a in enumerate(["start_evidence", "submit_for_review", "resolve_accept"], start=2):
        r = await client.patch(url, json={"action": a, "expected_version": i}, headers=auth())
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "RESOLVED_ACCEPTED"
    kinds = [e["kind"] for e in (await client.get(f"{url}/timeline", headers=auth())).json()]
    assert kinds == ["opened", "assigned", "start_evidence", "submit_for_review", "resolve_accept"]


async def test_withdraw(client: httpx.AsyncClient, auth: Auth) -> None:
    d = await _open(client, auth)
    r = await client.post(f"/v1/disputes/{d['id']}/withdraw?expected_version=1", headers=auth())
    assert r.json()["status"] == "WITHDRAWN"


async def test_evidence(client: httpx.AsyncClient, auth: Auth) -> None:
    d = await _open(client, auth)
    url = f"/v1/disputes/{d['id']}/evidence"
    r = await client.post(url, json={"kind": "delivery_proof", "description": "UPS POD signed"}, headers=auth())
    assert r.status_code == 201
    assert (await client.post(url, json={"kind": "selfie", "description": "nope"}, headers=auth())).status_code == 422
    assert [e["kind"] for e in (await client.get(url, headers=auth())).json()] == ["delivery_proof"]


async def test_list_filters_by_status(client: httpx.AsyncClient, auth: Auth) -> None:
    r = await client.get("/v1/disputes?status=UNDER_REVIEW", headers=auth())
    assert r.status_code == 200 and all(i["status"] == "UNDER_REVIEW" for i in r.json()["items"])


async def test_transaction_lookup_scoped(client: httpx.AsyncClient, auth: Auth) -> None:
    assert (await client.get("/v1/transactions/NW-1001", headers=auth())).json()["three_ds_authenticated"] is True
    assert (await client.get("/v1/transactions/NW-1001", headers=auth(tenant="aurora"))).status_code == 404


@pytest.mark.parametrize(
    ("role", "scope", "code"),
    [("analyst", "tenant", 403), ("ops_lead", "network", 403), ("ops_lead", "tenant", 201), ("admin", "network", 201)],
)
async def test_policy_ingest_permissions(
    client: httpx.AsyncClient, auth: Auth, role: str, scope: str, code: int
) -> None:
    body = {
        "title": "Test policy",
        "source": "t",
        "scope": scope,
        "text": "Gift cards are non-refundable once activated by the customer at checkout.",
    }
    r = await client.post("/v1/policies/documents", json=body, headers=auth(role=role))
    assert r.status_code == code, r.text


async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 200
