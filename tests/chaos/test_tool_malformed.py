"""T16.5: the payments server moves the money but answers malformed. The run must not claim a clean failure:
the side effect is 'unknown', and reconciliation finds the refund by its idempotency key."""

from __future__ import annotations

import uuid

from app.bootstrap.sweeper import Sweeper
from tests.graph.test_resolution_graph import dispute, sql, start


async def test_malformed_answer_after_refund_is_unknown_then_reconciled(client, auth, app, mcp_tools) -> None:
    mcp_tools["ledger"].faults.malformed_next("issue_refund")
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    ap = (await client.get(f"/v2/approvals/{v['approval']['approval_id']}", headers=auth(user="ben"))).json()
    out = (
        await client.post(
            f"/v2/approvals/{ap['id']}/decision",
            headers=auth(user="ben"),
            json={"decision": "approve", "summary_hash": ap["summary_hash"]},
        )
    ).json()["resolution"]
    assert out["outcome"] == "EXECUTION_FAILED" and "reconcile" in out["outcome_reason"]
    assert len(mcp_tools["ledger"].refunds) == 1  # the money did move
    assert sql("SELECT status FROM side_effects WHERE dispute_id = %s", uuid.UUID(did)) == [("unknown",)]
    sql("UPDATE side_effects SET updated_at = now() - interval '1 hour' WHERE dispute_id = %s", uuid.UUID(did))
    await Sweeper(app.state.container).run_once()
    assert sql("SELECT status, external_ref FROM side_effects WHERE dispute_id = %s", uuid.UUID(did)) == [
        ("succeeded", "rf_000001")
    ]
