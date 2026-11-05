"""G2/G3 against real runs: every node writes only keys it owns, the graph returns only ResolutionOut keys,
and the compiled topology matches the reviewed design."""

from __future__ import annotations

import typing
import uuid
from typing import Any

from langgraph.types import Command

from app.orchestration import state as S
from app.orchestration.runners import run_config
from tests.graph.test_resolution_graph import ctx, decision, dispute


async def test_nodes_write_only_owned_keys_and_output_is_out_schema(client, auth, tools, app) -> None:
    c = app.state.container
    graph = c.resolution_graph
    owners = S.OWNERS["resolution"]
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    tid = await c.graph_services.threads.start(
        tenant_id="northwind", dispute_id=uuid.UUID(did), graph="resolution", started_by="ana"
    )
    cfg = run_config(tid, c.limits, ctx=ctx(app, user="ana"))
    seen: dict[str, set[str]] = {}

    async def run(inp: Any, who: str) -> dict[str, Any]:
        final: dict[str, Any] = {}
        async for mode, chunk in graph.astream(inp, cfg, context=ctx(app, user=who), stream_mode=["updates", "values"]):
            if mode == "updates":
                for node, upd in chunk.items():
                    if node != "__interrupt__" and upd:
                        seen.setdefault(node, set()).update(upd)
            else:
                final = chunk
        return final

    await run({"dispute_id": did, "requested_by": "ana", "instructions": ""}, "ana")
    view = await c.resolution_runner().view(tid, ctx(app))
    await run(Command(resume=decision(view, "approved")), "ben")
    for node, keys in seen.items():
        assert keys <= owners.get(node, set()), f"{node} wrote {keys - owners.get(node, set())} it does not own"
    assert {"load_case", "gather_evidence", "execute", "apply_resolution"} <= set(seen)

    out = await graph.ainvoke(None, cfg, context=ctx(app))  # finished thread: returns the output schema only
    assert set(out) <= set(typing.get_type_hints(S.ResolutionOut))


def test_compiled_topology_matches_design(app) -> None:
    g = app.state.container.resolution_graph.get_graph()
    nodes = set(g.nodes) - {"__start__", "__end__"}
    assert nodes == {
        "load_case",
        "close_expired",
        "classify_reason",
        "gather_evidence",
        "escalate_insufficient",
        "draft_resolution",
        "verify",
        "escalate_unverified",
        "prepare_action",
        "request_info",
        "request_approval",
        "approve",
        "execute",
        "apply_resolution",
        "record_rejection",
        "record_expiry",
        "record_failure",
        "notify",
    }
    edges = {(e.source, e.target) for e in g.edges}
    assert ("verify", "draft_resolution") in edges and ("approve", "record_expiry") in edges
    assert ("notify", "__end__") in edges
    reach = {"__start__"}
    for _ in range(len(nodes)):
        reach |= {t for s, t in edges if s in reach}
    assert nodes <= reach  # no unreachable node
