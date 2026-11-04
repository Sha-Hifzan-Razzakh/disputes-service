"""Review R5: a stored decision whose resume could not run (thread busy, process full) is never stranded: the
thread becomes recoverable and admin recover replays the stored decision."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from tests.graph.test_resolution_graph import dispute, sql, start


async def _pending(client, auth) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    ap = (await client.get(f"/v2/approvals/{v['approval']['approval_id']}", headers=auth(user="ben"))).json()
    return v["thread_id"], ap


async def _decide(client, auth, ap: dict):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/v2/approvals/{ap['id']}/decision",
        headers=auth(user="ben"),
        json={"decision": "approve", "summary_hash": ap["summary_hash"]},
    )


@pytest.mark.parametrize("blocker", ["thread_busy", "too_many_runs"])
async def test_r5_decision_that_cannot_resume_becomes_recoverable(
    client, auth, app, tools, monkeypatch, blocker
) -> None:
    c = app.state.container
    tid, ap = await _pending(client, auth)
    if blocker == "thread_busy":
        monkeypatch.setattr(c, "limits", dataclasses.replace(c.limits, resume_lock_wait_s=0.05))
        async with c.graph_services.threads.exclusive(tid):  # e.g. the start run is still writing its interrupt
            r = await _decide(client, auth, ap)
        assert r.status_code == 409 and r.json()["error"]["code"] == "thread_busy"
    else:
        monkeypatch.setattr(c.settings, "admission_wait_s", 0.05)
        slots = asyncio.Semaphore(1)
        await slots.acquire()
        monkeypatch.setattr(c, "run_slots", slots)
        r = await _decide(client, auth, ap)
        assert r.status_code == 429
        slots.release()
    assert sql("SELECT status FROM threads WHERE thread_id = %s", tid) == [("resume_failed",)]
    assert (await _decide(client, auth, ap)).status_code == 409  # the decision is stored; it is never asked twice
    r = await client.post(f"/admin/threads/{tid}/recover", headers=auth(user="root", role="admin"))
    assert r.status_code == 200 and r.json()["outcome"] == "REFUNDED" and len(tools.calls) == 1


async def test_r5_waits_briefly_for_the_start_run_to_release_the_thread(client, auth, app, tools) -> None:
    c = app.state.container
    tid, ap = await _pending(client, auth)

    async def hold() -> None:
        async with c.graph_services.threads.exclusive(tid):
            await asyncio.sleep(0.3)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    r = await _decide(client, auth, ap)
    await holder
    assert r.status_code == 200 and r.json()["resolution"]["outcome"] == "REFUNDED"


async def test_r5_stranded_awaiting_thread_with_a_decided_approval_can_be_recovered(client, auth, app, tools) -> None:
    tid, ap = await _pending(client, auth)
    sql("UPDATE approvals SET status = 'approved', decided_by = 'ben', decided_at = now() WHERE id = %s", ap["id"])
    r = await client.post(f"/admin/threads/{tid}/recover", headers=auth(user="root", role="admin"))
    assert r.status_code == 200 and r.json()["outcome"] == "REFUNDED"


async def test_r16_thread_lock_is_not_held_inside_an_open_transaction(app) -> None:
    """Review R16: the lock connection must be idle, not 'idle in transaction', for the whole run, or
    idle_in_transaction_session_timeout / a transaction pooler would silently drop the lock."""
    async with app.state.container.graph_services.threads.exclusive("resolution:r16:1"):
        rows = sql(
            "SELECT a.state FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
            "WHERE l.locktype = 'advisory' AND l.objid = (hashtextextended('resolution:r16:1', 0) & 4294967295)::oid"
        )
    assert rows == [("idle",)]
