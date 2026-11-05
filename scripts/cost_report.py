"""Daily model spend per tenant (T25.9) from Prometheus (`model_tokens_total`, `model_cost_usd_total`).

    PROM_URL=http://prometheus:9090 uv run python scripts/cost_report.py [--days 7]
Prints one Markdown table row per tenant and day. Spike alerting is ModelCostSpike (ops/alerts).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx

QUERIES = {
    "cost_usd": "sum by (tenant) (increase(model_cost_usd_total[1d]))",
    "tokens_in": 'sum by (tenant) (increase(model_tokens_total{direction="in"}[1d]))',
    "tokens_out": 'sum by (tenant) (increase(model_tokens_total{direction="out"}[1d]))',
}


def report(prom: str, days: int, client: httpx.Client | None = None) -> str:
    c = client or httpx.Client(timeout=10)
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for name, q in QUERIES.items():
        r = c.get(
            f"{prom}/api/v1/query_range",
            params={
                "query": q,
                "start": (end - timedelta(days=days)).timestamp(),
                "end": end.timestamp(),
                "step": "1d",
            },
        )
        r.raise_for_status()
        for series in r.json()["data"]["result"]:
            for ts, value in series["values"]:
                # increase(...[1d]) evaluated at midnight of D covers day D-1
                day = (datetime.fromtimestamp(float(ts), UTC) - timedelta(days=1)).date().isoformat()
                rows[(day, series["metric"].get("tenant", "unknown"))][name] = float(value)
    out = ["| day | tenant | cost (USD) | tokens in | tokens out |", "|---|---|---|---|---|"]
    for (day, tenant), v in sorted(rows.items()):
        cost, tin, tout = v.get("cost_usd", 0), v.get("tokens_in", 0), v.get("tokens_out", 0)
        out.append(f"| {day} | {tenant} | {cost:.2f} | {tin:.0f} | {tout:.0f} |")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    prom = os.environ.get("PROM_URL")
    if not prom:
        sys.exit("set PROM_URL")
    print(report(prom, a.days))
