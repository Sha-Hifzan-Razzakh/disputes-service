"""Online evaluation of production runs (T25.6) and capture of failures into the golden set (T25.7).

    uv run python -m evals.online score --hours 24 [--push http://pushgateway:9091]
    uv run python -m evals.online capture <thread_id> [<thread_id> ...]

`score` reads finished resolution threads from the database and their final checkpoints, computes label-free
checks (evals/online_thresholds.yaml) and exits 1 when a check is under its minimum. With --push it publishes
`online_eval_score{check}` and `online_eval_threshold{check}` for the OnlineEvalDrift alert.

`capture` turns a production thread into a golden-set candidate: the case facts the rules look at (no ids, refs or
free text except the analyst note), labelled by the independent oracle, plus what production actually did. A
disagreement is a failure class; the candidate goes to evals/candidates.jsonl for review before it joins
golden_vN (docs/runbook.md, "Online eval drift").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from app.infrastructure.db.models import ApprovalRow, ThreadRow
from evals.oracle import expected

THRESHOLDS = Path(__file__).with_name("online_thresholds.yaml")
CANDIDATES = Path(__file__).with_name("candidates.jsonl")
SUCCESS = {"REFUNDED", "PARTIALLY_REFUNDED", "REPRESENTED"}


def _ratio(good: int, total: int) -> float | None:
    return None if total == 0 else round(good / total, 4)


async def _states(c: Any, thread_ids: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    for tid in thread_ids:
        snap = await c.resolution_graph.aget_state({"configurable": {"thread_id": tid}})
        out[tid] = snap.values or {}
    return out


async def score(c: Any, *, since: datetime, tenant: str | None = None) -> dict[str, Any]:
    tq = select(ThreadRow).where(
        ThreadRow.graph == "resolution", ThreadRow.status == "done", ThreadRow.updated_at >= since
    )
    aq = select(ApprovalRow.status).where(
        ApprovalRow.status.in_(("approved", "rejected")), ApprovalRow.decided_at >= since
    )
    if tenant:
        tq, aq = tq.where(ThreadRow.tenant_id == tenant), aq.where(ApprovalRow.tenant_id == tenant)
    async with c.sessions() as s:
        threads = (await s.scalars(tq)).all()
        decided = (await s.scalars(aq)).all()
    states = await _states(c, [t.thread_id for t in threads])
    drafted = [v for v in states.values() if v.get("draft") and v["draft"].get("resolution") != "REQUEST_INFO"]
    grounded = sum(
        1 for v in drafted if set(v["draft"].get("citations") or ()) <= {e["id"] for e in v.get("evidence") or ()}
    )
    verified = [v for v in states.values() if v.get("verification")]
    first_try = sum(1 for v in verified if v["verification"].get("passed") and not v.get("refine_attempts"))
    outcomes = [t.outcome or "" for t in threads]
    executed = [o for o in outcomes if o in SUCCESS or o == "EXECUTION_FAILED"]
    scores = {
        "grounded": _ratio(grounded, len(drafted)),
        "verified_first_draft": _ratio(first_try, len(verified)),
        "approval_acceptance": _ratio(sum(1 for d in decided if d == "approved"), len(decided)),
        "execution_success": _ratio(sum(1 for o in executed if o in SUCCESS), len(executed)),
        "non_abstention": _ratio(sum(1 for o in outcomes if not o.startswith("ABSTAINED")), len(outcomes)),
    }
    cfg = yaml.safe_load(THRESHOLDS.read_text())
    enough = len(threads) >= cfg["min_runs"]
    below = {k: v for k, v in scores.items() if enough and v is not None and v < cfg["checks"][k]}
    return {
        "since": since.isoformat(),
        "runs": len(threads),
        "scores": scores,
        "thresholds": cfg["checks"],
        "verdict": "insufficient_data" if not enough else ("drift" if below else "ok"),
        "below": below,
    }


def push(report: dict[str, Any], url: str) -> None:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

    reg = CollectorRegistry()
    score_g = Gauge("online_eval_score", "Online check score over the window", ["check"], registry=reg)
    thr_g = Gauge("online_eval_threshold", "Minimum for the online check", ["check"], registry=reg)
    for k, v in report["scores"].items():
        if v is not None and report["verdict"] != "insufficient_data":
            score_g.labels(k).set(v)
            thr_g.labels(k).set(report["thresholds"][k])
    push_to_gateway(url, job="online_eval", registry=reg)


async def capture(c: Any, thread_id: str) -> dict[str, Any]:
    async with c.sessions() as s:
        t = await s.get(ThreadRow, thread_id)
    if t is None:
        raise SystemExit(f"thread {thread_id} not found")
    v = (await c.resolution_graph.aget_state({"configurable": {"thread_id": thread_id}})).values or {}
    snap = await c.graph_services.cases.snapshot(t.tenant_id, t.dispute_id)
    d, txn = snap.dispute, snap.transaction
    case: dict[str, Any] = {
        "id": f"P-{thread_id.split(':')[1][:8]}-{thread_id.rsplit(':', 1)[1]}",
        "tenant": "evalco",
        "currency": d.amount.currency,
        "approval_threshold": "250.00",
        "network": d.network.value,
        "reason_code": d.reason_code,
        "disputed_amount": str(d.amount.amount),
        "transaction": {
            "amount": str(txn.amount.amount),
            "three_ds_authenticated": txn.three_ds_authenticated,
            "avs_result": txn.avs_result,
            "cvv_result": txn.cvv_result,
            "delivery_status": txn.delivery_status,
            "delivery_tracking": "TRACKED" if txn.delivery_tracking else None,
            "refunded": str(txn.refunded.amount),
        },
        "merchant_evidence": sorted({e.kind for e in snap.evidence}),
        "days_to_deadline": round((d.respond_by - t.created_at).total_seconds() / 86400, 2),
        "instructions": v.get("instructions") or None,
        "fixture": None,
        "tags": ["production"],
        "source": "production",
    }
    case["expected"] = expected(case)
    draft = v.get("draft") or {}
    actual = {
        "outcome": v.get("outcome") or t.outcome,
        "resolution": draft.get("resolution"),
        "amount": draft.get("amount"),
    }
    exp = case["expected"]
    stop = (
        actual["outcome"]
        if actual["outcome"] in {"INFO_REQUESTED", "DEADLINE_PASSED"} or str(actual["outcome"]).startswith("ABSTAINED")
        else "AWAITING_APPROVAL"
    )
    case["production"] = actual
    case["note"] = (
        "agrees with the oracle"
        if stop == exp["outcome"] and (exp.get("resolution") in (None, actual["resolution"]))
        else f"DISAGREES: oracle {exp['outcome']} {exp.get('resolution')}, production {actual['outcome']} {actual['resolution']}"
    )
    return case


async def _main() -> int:
    from fastapi import FastAPI

    from app.bootstrap.lifespan import lifespan

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--hours", type=float, default=24)
    sc.add_argument("--push", default=None)
    sc.add_argument("--tenant", default=None)
    cp = sub.add_parser("capture")
    cp.add_argument("thread_ids", nargs="+")
    a = ap.parse_args()
    app = FastAPI()
    async with lifespan(app, start_sweeper=False):
        c = app.state.container
        if a.cmd == "score":
            report = await score(c, since=datetime.now(UTC) - timedelta(hours=a.hours), tenant=a.tenant)
            print(json.dumps(report, indent=2))
            if a.push:
                push(report, a.push)
            return 1 if report["verdict"] == "drift" else 0
        with CANDIDATES.open("a") as f:
            for tid in a.thread_ids:
                case = await capture(c, tid)
                f.write(json.dumps(case, sort_keys=True) + "\n")
                print(case["id"], case["note"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
