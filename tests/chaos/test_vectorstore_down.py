"""T16.3: the policy store fails mid-run. Short outages are absorbed by retrieve_policy's RetryPolicy; a long one
fails the run, which recovers from the last checkpoint once the store is back (no step before it re-runs)."""

from __future__ import annotations

import pytest

from app.application.ports import TransientError
from tests.graph.test_resolution_graph import ctx, dispute, sql, start


async def test_brief_outage_is_absorbed(client, auth, app, tools, monkeypatch) -> None:
    store = app.state.container.graph_services.policy_store
    real, failures = store.search, {"n": 2}

    async def flaky(**kw):  # type: ignore[no-untyped-def]
        if failures["n"]:
            failures["n"] -= 1
            raise TransientError("connection reset")
        return await real(**kw)

    monkeypatch.setattr(store, "search", flaky)
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    v = (await start(client, auth, did)).json()
    assert failures["n"] == 0 and v["outcome"] == "AWAITING_APPROVAL"


async def test_long_outage_fails_then_recovers_from_checkpoint(client, auth, app, tools, monkeypatch) -> None:
    store = app.state.container.graph_services.policy_store
    real = store.search

    async def down(**kw):  # type: ignore[no-untyped-def]
        raise TransientError("vector store unavailable")

    monkeypatch.setattr(store, "search", down)
    did = await dispute(client, auth, "NW-1001", "10.4", "189.00")
    with pytest.raises(TransientError):
        await start(client, auth, did)
    tid = sql("SELECT thread_id FROM threads WHERE dispute_id = %s", did)[0][0]
    monkeypatch.setattr(store, "search", real)
    out = await app.state.container.resolution_runner().recover(tid, ctx(app, user="ana"))
    assert out["outcome"] == "AWAITING_APPROVAL"
