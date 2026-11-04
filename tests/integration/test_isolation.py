"""T15.9 tenant isolation: every v2/admin route, retrieval scope and the tool gateway."""

from __future__ import annotations

import uuid

import pytest

from tests.graph.test_resolution_graph import dispute, sql, start


@pytest.fixture
async def northwind_pending(client, auth, mcp_tools) -> dict:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    v = (await start(client, auth, did)).json()
    return {"did": did, "tid": v["thread_id"], "aid": v["approval"]["approval_id"]}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v2/disputes/{did}/resolutions"),
        ("GET", "/v2/disputes/{did}/resolutions/{tid}"),
        ("GET", "/v2/disputes/{did}/resolutions/{tid}/stream"),
        ("GET", "/v2/approvals/{aid}"),
        ("POST", "/v2/approvals/{aid}/decision"),
        ("GET", "/admin/threads/{tid}/history"),
        ("POST", "/admin/threads/{tid}/recover"),
        ("POST", "/admin/threads/{tid}/cancel"),
    ],
)
async def test_other_tenant_gets_404_everywhere(client, auth, northwind_pending, method: str, path: str) -> None:
    url = path.format(**northwind_pending)
    body = {"decision": "approve", "summary_hash": "a" * 64} if "decision" in path else {}
    r = await client.request(method, url, json=body, headers=auth(user="zoe", tenant="aurora", role="admin"))
    assert r.status_code == 404, (url, r.status_code, r.text)
    assert sql("SELECT status FROM approvals WHERE id = %s", uuid.UUID(northwind_pending["aid"])) == [("pending",)]


async def test_retrieval_never_returns_another_tenants_merchant_policy(client, auth, app, mcp_tools) -> None:
    """Aurora's policy talks about restocking fees; a northwind run asking about exactly that must only ever see
    northwind merchant policy and shared network rules."""
    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")
    tid = (
        await start(client, auth, did, instructions="restocking fee opened electronics returns within 14 days")
    ).json()["thread_id"]
    values = (await app.state.container.resolution_graph.aget_state({"configurable": {"thread_id": tid}})).values
    chunk_ids = [uuid.UUID(e["id"].removeprefix("policy:")) for e in values["evidence"] if e["source"] == "policy"]
    assert chunk_ids, "the run retrieved no policy at all"
    owners = {t for (t,) in sql("SELECT DISTINCT tenant_id FROM policy_chunks WHERE id = ANY(%s)", chunk_ids)}
    assert owners <= {"northwind", None}, owners
    assert sql("SELECT count(*) FROM policy_chunks WHERE tenant_id = 'aurora' AND text ILIKE '%%restocking%%'") != [
        (0,)
    ]


async def test_gateway_refuses_cross_tenant_arguments(mcp_tools) -> None:
    res = await mcp_tools["gateway"].call(
        "payments.get_transaction", {"tenant_id": "aurora", "transaction_ref": "AU-2001"}, tenant_id="northwind"
    )
    assert not res.ok and "tenant mismatch" in res.error
