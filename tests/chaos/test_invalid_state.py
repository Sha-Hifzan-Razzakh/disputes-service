"""T16.6: invalid state is loud, never silently accepted."""

from __future__ import annotations

import pytest
from langgraph.errors import InvalidUpdateError

from app.orchestration.reducers import OutcomeAlreadySet
from app.orchestration.resolution import InvalidResume
from tests.graph.test_resolution_graph import ctx, decision, dispute, start


async def test_second_terminal_outcome_is_rejected(client, auth, app, tools) -> None:
    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")  # ends INFO_REQUESTED
    tid = (await start(client, auth, did)).json()["thread_id"]
    g = app.state.container.resolution_graph
    with pytest.raises((InvalidUpdateError, OutcomeAlreadySet)):
        await g.aupdate_state({"configurable": {"thread_id": tid}}, {"outcome": "REFUNDED"}, as_node="apply_resolution")


async def test_forged_resume_values_are_rejected(client, auth, app, tools) -> None:
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    runner = app.state.container.resolution_runner()
    for forged in (
        {"v": 2, "approval_id": v["approval"]["approval_id"], "decision": "approved"},
        decision(v, "approved") | {"approval_id": "someone-elses"},
        decision(v, "pay-double"),
    ):
        with pytest.raises(InvalidResume):
            await runner.resume(v["thread_id"], forged, ctx(app))
    assert tools.calls == []
