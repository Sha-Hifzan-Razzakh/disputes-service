"""run_and_trace (T15.2): run a graph and return its final state plus the node path it took, from stream updates.
Used by scenario tests and the eval trajectory scorer."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.orchestration.context import Ctx
from app.orchestration.evidence import build_evidence_graph
from app.orchestration.resolution import build_resolution_graph


def memory_resolution_graph() -> Any:
    """Resolution graph on an in-memory checkpointer: for fast scenario tests with tests/fakes.py services."""
    return build_resolution_graph(build_evidence_graph().compile()).compile(checkpointer=InMemorySaver())


async def run_and_trace(graph: Any, inp: Any, thread_id: str, ctx: Ctx, **cfg: Any) -> tuple[dict[str, Any], list[str]]:
    path: list[str] = []
    config = {"configurable": {"thread_id": thread_id}, **cfg}
    async for mode, chunk in graph.astream(inp, config, context=ctx, stream_mode=["updates", "values"]):
        if mode == "updates":
            path += [n for n in chunk if n != "__interrupt__"]
    state = await graph.aget_state(config)
    return dict(state.values) | {"__next__": list(state.next)}, path
