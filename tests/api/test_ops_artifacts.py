"""Dashboards and alert rules only reference series the service really exports (stage 13 guard)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tests.graph.test_resolution_graph import dispute, start

SERIES = re.compile(r"\b([a-z_]+_(?:total|bucket|count|sum))\b")


def referenced() -> set[str]:
    exprs = [
        t["expr"]
        for p in json.loads(Path("ops/dashboards/resolution.json").read_text())["panels"]
        for t in p["targets"]
    ]
    for g in yaml.safe_load(Path("ops/alerts/disputes.rules.yml").read_text())["groups"]:
        exprs += [r["expr"] for r in g["rules"]]
    return {m for e in exprs for m in SERIES.findall(e)}


async def test_every_referenced_series_is_exported(client, auth, app, mcp_tools) -> None:
    import dataclasses

    from tests.graph.fakes import FailOnce

    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")
    await start(client, auth, did)  # outcome + node latency series
    await mcp_tools["gateway"].call("payments.get_transaction", {"transaction_ref": "NW-1001"}, tenant_id="northwind")
    c = app.state.container  # a node that raises once → graph_node_errors series
    ctx = c.graph_context(tenant_id="northwind", user_id="ana", role="analyst", request_id="ops-artifacts")
    ctx = dataclasses.replace(
        ctx,
        services=dataclasses.replace(
            ctx.services, cases=FailOnce(ctx.services.cases, "snapshot", RuntimeError("boom"))
        ),
    )
    did2 = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    with pytest.raises(RuntimeError):
        await c.resolution_graph.ainvoke(
            {"dispute_id": did2, "requested_by": "ana"}, {"configurable": {"thread_id": f"ops:{did2}"}}, context=ctx
        )
    text = (await client.get("/metrics")).text
    exported = {line.split("{")[0].split(" ")[0] for line in text.splitlines() if line and not line.startswith("#")}
    missing = referenced() - exported
    assert not missing, f"dashboards/alerts reference unknown series: {sorted(missing)}"
