"""T17.10 ops: latency, tool calls, tokens and cost per case (tokens/cost are 0 with the rules drafter)."""

from __future__ import annotations

import statistics
from typing import Any


def aggregate(records: list[dict[str, Any]]) -> dict[str, float]:
    lat = sorted(r["ms"] for r in records)
    q = statistics.quantiles(lat, n=20) if len(lat) > 1 else lat * 19
    return {
        "latency_p50_ms": round(statistics.median(lat), 1),
        "latency_p95_ms": round(q[18], 1),
        "tool_calls_per_case": round(sum(len(r["tool_calls"]) for r in records) / len(records), 3),
        "tokens_per_case": round(sum(r.get("tokens", 0) for r in records) / len(records), 1),
        "cost_usd_per_case": round(sum(r.get("cost_usd", 0.0) for r in records) / len(records), 5),
    }
