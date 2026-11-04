"""T16.10: two workers resume the same thread at once (bypassing the approvals CAS). The advisory lock lets one
run; the other gets ThreadBusy; exactly one refund."""

from __future__ import annotations

import asyncio

from app.application.errors import ThreadBusy
from tests.graph.test_resolution_graph import ctx, decision, dispute, start


async def test_one_resume_wins_the_thread_lock(client, auth, app, tools) -> None:
    tools.delay_s = 0.3
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    runner = app.state.container.resolution_runner()
    results = await asyncio.gather(
        *(runner.resume(v["thread_id"], decision(v, "approved"), ctx(app)) for _ in range(2)), return_exceptions=True
    )
    busy = [r for r in results if isinstance(r, ThreadBusy)]
    done = [r for r in results if isinstance(r, dict)]
    assert len(busy) == 1 and len(done) == 1 and done[0]["outcome"] == "REFUNDED"
    assert len(tools.effects) == 1 and len(tools.calls) == 1
