"""T16.10: two workers resume the same thread at once (bypassing the approvals CAS). The advisory lock lets one
run at a time: the other waits (review R5) and then finds nothing left to do, or gets ThreadBusy when its wait runs
out. Either way exactly one refund."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from app.application.errors import ThreadBusy
from tests.graph.test_resolution_graph import ctx, decision, dispute, start


@pytest.mark.parametrize("wait_s", [5.0, 0.05])
async def test_one_resume_wins_the_thread_lock(client, auth, app, tools, monkeypatch, wait_s: float) -> None:
    tools.delay_s = 0.3
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    runner = app.state.container.resolution_runner()
    monkeypatch.setattr(runner, "limits", dataclasses.replace(runner.limits, resume_lock_wait_s=wait_s))
    results = await asyncio.gather(
        *(runner.resume(v["thread_id"], decision(v, "approved"), ctx(app)) for _ in range(2)), return_exceptions=True
    )
    busy = [r for r in results if isinstance(r, ThreadBusy)]
    done = [r for r in results if isinstance(r, dict)]
    assert all(d["outcome"] == "REFUNDED" for d in done) and len(busy) == (1 if wait_s < 0.3 else 0), results
    assert len(tools.effects) == 1 and len(tools.calls) == 1
