"""Review findings R1, R2, R7: money moves at most once per dispute, only for the case that was approved, and an
approval is not spent on a call that was never sent."""

from __future__ import annotations

import pytest

from tests.graph.test_resolution_graph import ctx, decision, dispute, sql, start


async def test_r1_unsettled_side_effect_blocks_a_second_resolution(client, auth, tools, app) -> None:
    """A refund whose outcome is unknown may have happened; a new thread would get a new idempotency key."""
    tools.malformed = True  # provider records the refund, the answer is unreadable → unknown
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "EXECUTION_FAILED" and sql(
        "SELECT status FROM side_effects WHERE dispute_id = %s", did
    ) == [("unknown",)]
    r = await start(client, auth, did)
    assert r.status_code == 409 and r.json()["error"]["code"] == "side_effect_unsettled"
    assert sql("SELECT count(*) FROM threads WHERE dispute_id = %s", did) == [(1,)]
    sql("UPDATE side_effects SET status = 'failed' WHERE dispute_id = %s", did)  # reconciliation: not found
    tools.malformed = False
    assert (await start(client, auth, did)).status_code == 202


@pytest.mark.parametrize("change", ["resolve_accept", "withdraw"])
async def test_r2_case_changed_after_approval_sends_nothing(client, auth, tools, app, change: str) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50", review=change == "resolve_accept")
    v = (await start(client, auth, did)).json()
    if change == "withdraw":  # withdrawn by the cardholder while the approval waited (EVIDENCE_GATHERING)
        r = await client.post(f"/v1/disputes/{did}/withdraw?expected_version=2", headers=auth())
    else:  # refunded by hand in the console, then resolved through v1 while the approval waited
        r = await client.patch(f"/v1/disputes/{did}", json={"action": change, "expected_version": 3}, headers=auth())
    assert r.status_code == 200, r.text
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "EXECUTION_FAILED" and "case changed after approval" in out["outcome_reason"]
    assert tools.calls == [] and sql("SELECT count(*) FROM side_effects WHERE dispute_id = %s", did) == [(0,)]


async def test_r7_call_refused_before_sending_is_retried_not_spent(client, auth, tools, app) -> None:
    tools.not_attempted = 1  # e.g. per-tenant rate limit: nothing reached the provider
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    out = await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    assert out["outcome"] == "REFUNDED" and len(tools.calls) == 2
    assert sql("SELECT status FROM side_effects WHERE dispute_id = %s", did) == [("succeeded",)]
