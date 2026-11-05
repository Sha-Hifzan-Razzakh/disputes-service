"""T10.13 admin inspection, T10.11 retention, T09.9 recursion abort → 500."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.bootstrap.settings import get_settings
from app.orchestration.runners import RunLimits
from scripts.prune_checkpoints import prune
from tests.graph.test_resolution_graph import dispute, sql, start


async def test_history_is_admin_only_tenant_scoped_and_value_free(client, auth, tools) -> None:
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    tid = (await start(client, auth, did)).json()["thread_id"]
    assert (await client.get(f"/admin/threads/{tid}/history", headers=auth(role="ops_lead"))).status_code == 403
    assert (
        await client.get(f"/admin/threads/{tid}/history", headers=auth(tenant="aurora", role="admin"))
    ).status_code == 404
    r = await client.get(f"/admin/threads/{tid}/history?limit=50", headers=auth(role="admin"))
    assert r.status_code == 200
    cps = r.json()["checkpoints"]
    assert cps[0]["interrupted"] and cps[0]["next"] == ["approve"]
    ran = {r["node"]: r["wrote"] for c in cps for r in c["ran"]}
    assert ran["gather_evidence"] == ["evidence", "evidence_grade", "queries", "retrieval_attempts"]
    assert all(
        set(c) == {"checkpoint_id", "step", "source", "ran", "next", "created_at", "outcome", "interrupted"}
        for c in cps
    )  # no state values leave through this endpoint
    assert sql(
        "SELECT count(*) FROM audit_log WHERE action = 'admin.history_viewed' AND subject = %s", f"thread:{tid}"
    ) == [(1,)]


async def test_retention_prunes_only_old_finished_threads(client, auth, tools, app) -> None:
    graph = app.state.container.resolution_graph
    done = await dispute(client, auth, "NW-1005", "13.3", "75.00")  # ends INFO_REQUESTED
    done_tid = (await start(client, auth, done)).json()["thread_id"]
    waiting = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    wait_tid = (await start(client, auth, waiting)).json()["thread_id"]
    sql("UPDATE threads SET updated_at = now() - interval '200 days' WHERE thread_id IN (%s, %s)", done_tid, wait_tid)

    pruned = await prune(get_settings(), now=datetime.now(UTC))
    assert done_tid in pruned and wait_tid not in pruned
    cfg = lambda t: {"configurable": {"thread_id": t}}  # noqa: E731
    assert (await graph.aget_state(cfg(done_tid))).values == {}
    assert (await graph.aget_state(cfg(wait_tid))).next == ("approve",)
    assert sql("SELECT outcome FROM threads WHERE thread_id = %s", done_tid) == [("INFO_REQUESTED",)]  # record kept


async def test_recursion_abort_is_a_500_with_alert(client, auth, tools, app, monkeypatch) -> None:
    c = app.state.container
    monkeypatch.setattr(c, "limits", RunLimits(recursion_limit=3))
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    r = await start(client, auth, did)
    assert r.status_code == 500 and r.json()["error"]["code"] == "run_aborted"
    assert sql("SELECT status FROM threads WHERE dispute_id = %s", uuid.UUID(did)) == [("failed",)]
