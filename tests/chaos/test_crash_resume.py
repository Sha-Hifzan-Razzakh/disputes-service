"""T16.8: the worker dies (task cancelled) inside execute after the provider recorded the refund. The thread is
left 'running' with a pending side effect; recovery re-runs execute with the same key and the provider dedupes."""

from __future__ import annotations

import asyncio

from tests.graph.test_resolution_graph import ctx, decision, dispute, sql, start


async def test_cancelled_mid_execute_after_the_refund_recovers_once(client, auth, app, tools) -> None:
    tools.delay_s = 0.5  # the refund is recorded, then the answer takes long enough to be cut off
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    runner = app.state.container.resolution_runner()
    task = asyncio.create_task(runner.resume(v["thread_id"], decision(v, "approved"), ctx(app)))
    for _ in range(500):  # wait until the provider has recorded the refund
        if tools.effects:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sql("SELECT status FROM side_effects WHERE dispute_id = %s", did) == [("pending",)]
    tools.delay_s = 0
    out = await runner.recover(v["thread_id"], ctx(app))
    assert out["outcome"] == "REFUNDED" and len(tools.effects) == 1 and len(tools.calls) == 2
