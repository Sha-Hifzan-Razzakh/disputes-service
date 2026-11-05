"""Quarterly review input (T26.7): graph nodes that did not run in the window, from Prometheus.

    PROM_URL=http://prometheus:9090 uv run python scripts/node_usage.py [--days 90]
Compares `graph_node_duration_milliseconds_count` by node with the compiled graphs' node lists. A node that never
ran is a candidate for removal, or proof that a path (e.g. a rare outcome) needs a synthetic or golden case.
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

from app.orchestration.evidence import build_evidence_graph
from app.orchestration.resolution import build_resolution_graph


def compiled_nodes() -> dict[str, set[str]]:
    ev = build_evidence_graph().compile(name="evidence")
    res = build_resolution_graph(ev).compile(name="resolution")
    return {
        "resolution": {n for n in res.get_graph().nodes if not n.startswith("__")} - {"gather_evidence"},
        "evidence": {n for n in ev.get_graph().nodes if not n.startswith("__")},
    }


def unused(prom: str, days: int, client: httpx.Client | None = None) -> dict[str, list[str]]:
    c = client or httpx.Client(timeout=10)
    q = f"sum by (graph, node) (increase(graph_node_duration_milliseconds_count[{days}d]))"
    r = c.get(f"{prom}/api/v1/query", params={"query": q})
    r.raise_for_status()
    ran = {
        (s["metric"].get("graph"), s["metric"].get("node"))
        for s in r.json()["data"]["result"]
        if float(s["value"][1]) > 0
    }
    return {g: sorted(n for n in nodes if (g, n) not in ran) for g, nodes in compiled_nodes().items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    a = ap.parse_args()
    prom = os.environ.get("PROM_URL") or sys.exit("set PROM_URL")
    for graph, nodes in unused(prom, a.days).items():
        print(f"{graph}: {', '.join(nodes) or 'every node ran'}")
