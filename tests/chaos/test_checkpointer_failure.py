"""T16.7: the checkpoint write after `execute` fails. The refund happened but the graph does not know; recovery
re-runs execute, which finds the side effect already succeeded and makes no second call."""

from __future__ import annotations

from tests.graph.test_resolution_graph import ctx, decision, dispute, sql, start


async def test_lost_checkpoint_after_execute_does_not_repeat_the_refund(client, auth, app, tools, monkeypatch) -> None:
    graph = app.state.container.resolution_graph
    saver = graph.checkpointer
    real_aput, failed = saver.aput, {"done": False}

    async def aput(config, checkpoint, metadata, new_versions):  # type: ignore[no-untyped-def]
        if not failed["done"] and "execution" in checkpoint["channel_versions"] and "execute" in str(metadata):
            failed["done"] = True
            raise ConnectionError("checkpoint write lost")
        return await real_aput(config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(saver, "aput", aput)
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    v = (await start(client, auth, did)).json()
    try:
        await app.state.container.resolution_runner().resume(v["thread_id"], decision(v, "approved"), ctx(app))
    except ConnectionError:
        pass
    monkeypatch.setattr(saver, "aput", real_aput)
    if failed["done"]:
        out = await app.state.container.resolution_runner().recover(v["thread_id"], ctx(app))
    else:  # the saver wrote that step through another path; the run simply finished
        out = await app.state.container.resolution_runner().view(v["thread_id"], ctx(app))
    assert out["outcome"] == "REFUNDED"
    assert len(tools.effects) == 1
    assert sql("SELECT count(*) FROM side_effects WHERE dispute_id = %s", did) == [(1,)]
