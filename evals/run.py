"""Eval runner (T17.11-T17.13).

For every golden case: create the transaction, dispute and merchant uploads in a dedicated `evalco` tenant of a
fresh eval database; run the real resolution graph (real adapters, retrieval and checkpointer) to its first stop
and record the node path; if it pauses, decide the approval through ApprovalService and resume, executing against
the in-memory MCP servers. Score, aggregate, compare with thresholds and the baseline, write a report.

    uv run python -m evals.run                          # golden_v1, compare with the latest baseline
    uv run python -m evals.run --dataset evals/golden_v0.jsonl
    uv run python -m evals.run --write-baseline 2.0.0-rules
Exit code 1 when a threshold or a baseline comparison fails.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

BASE_DSN = os.environ.get("APP_TEST_DSN", "postgresql://postgres@localhost:5432")
EVAL_DB = "disputes_eval"
APPROVER = "eval-approver"
REQUESTER = "eval-analyst"
EVALCO_POLICY = (
    "Evalco returns and dispute policy",
    "evalco-policy-v1",
    "Evalco accepts returns within 30 days. For not-as-described claims where the product description is on file, "
    "Evalco offers a partial refund of fifty percent and contests the remainder with the description as evidence.\n\n"
    "When both the product description and the customer communications support the sale, Evalco contests the "
    "dispute. Refunds of 250 USD or more require an operations lead.",
)


# --- environment ------------------------------------------------------------------------------------------------


def prepare_database() -> str:
    import psycopg
    from alembic import command
    from alembic.config import Config

    with psycopg.connect(f"{BASE_DSN}/postgres", autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {EVAL_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {EVAL_DB}")
    url = f"{BASE_DSN.replace('postgresql://', 'postgresql+psycopg://')}/{EVAL_DB}"
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    os.environ.update(
        APP_DATABASE_URL=url,
        APP_MODEL_PROVIDER=os.environ.get("APP_MODEL_PROVIDER", "rules"),
        APP_SWEEPER_ENABLED="false",
        APP_TOOLS_ENABLED="false",
    )
    return url


async def seed(c: Any) -> None:
    from app.infrastructure.db.models import TenantDisputePolicyRow, TenantRow
    from seeds.load import load

    await load(c.sessions)  # northwind, aurora and the shared network-rule summaries
    async with c.sessions.begin() as s:
        s.add(TenantRow(id="evalco", name="Evalco Ltd", currency="USD"))
        await s.flush()
        s.add(TenantDisputePolicyRow(tenant_id="evalco", approval_threshold=250, partial_pct=50))
    await c.policy_store.add_document(
        tenant_id="evalco", title=EVALCO_POLICY[0], source=EVALCO_POLICY[1], text=EVALCO_POLICY[2]
    )


STORE_FIXTURES = {"empty_policy_store", "network_docs_only", "low_scores_first_round"}


# --- per-case test doubles (golden `fixture` field) ---------------------------------------------------------------


class FilteredStore:
    def __init__(self, inner: Any, mode: str) -> None:
        self.inner, self.mode, self.calls = inner, mode, 0

    async def add_document(self, **kw: Any) -> int:
        return int(await self.inner.add_document(**kw))

    async def search(self, *, tenant_id: str, query: str, k: int) -> list[Any]:
        self.calls += 1
        if self.mode == "empty_policy_store":
            return []
        hits = await self.inner.search(tenant_id=tenant_id, query=query, k=k)
        if self.mode == "network_docs_only":
            return [h for h in hits if h.scope == "network"]
        if self.mode == "low_scores_first_round" and self.calls <= 2:
            return [dataclasses.replace(h, score=0.01) for h in hits]
        return list(hits)


class DrafterDouble:
    def __init__(self, inner: Any, mode: str) -> None:
        self.inner, self.mode, self.calls = inner, mode, 0

    async def draft(self, **kw: Any) -> Any:
        from app.application.ports import DraftResolution
        from app.domain.money import Money

        self.calls += 1
        if self.mode == "drafter_overpays":
            return DraftResolution("ACCEPT", Money.of("999.00", "USD"), "refund everything", ("ledger:three_ds",))
        d = await self.inner.draft(**kw)
        if self.mode == "drafter_forgets_citations_once" and self.calls == 1:
            return dataclasses.replace(d, citations=())
        return d


class RecordingTools:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    async def call(self, tool: str, args: dict[str, Any], **kw: Any) -> Any:
        self.calls.append({"tool": tool, "amount": args.get("amount"), "evidence_ids": args.get("evidence_ids")})
        return await self.inner.call(tool, args, **kw)


# --- one case -----------------------------------------------------------------------------------------------------


async def run_case(c: Any, gateway: Any, ledger: Any, case: dict[str, Any]) -> dict[str, Any]:
    from langgraph.types import Command
    from sqlalchemy import text

    from app.application.approvals import ApprovalService, resume_value, summary_hash
    from app.application.services import DisputeService, SystemClock
    from app.domain.money import Money
    from app.infrastructure.db.models import TransactionRow
    from app.infrastructure.repositories import SqlDisputeRepository, SqlEvidenceRepository, SqlLedger
    from evals.oracle import FAMILY

    now = datetime.now(UTC)
    t = case["transaction"]
    ref = f"EV-{case['id']}-{uuid.uuid4().hex[:6]}"
    async with c.sessions.begin() as s:
        s.add(
            TransactionRow(
                tenant_id="evalco",
                ref=ref,
                amount=t["amount"],
                currency="USD",
                captured_at=now,
                card_brand=case["network"],
                avs_result=t["avs_result"],
                cvv_result=t["cvv_result"],
                three_ds_authenticated=t["three_ds_authenticated"],
                delivery_status=t["delivery_status"],
                delivery_tracking=t["delivery_tracking"],
                refunded_amount=t["refunded"],
            )
        )
    ledger.transactions[("evalco", ref)] = {
        "ref": ref,
        "amount": Decimal(t["amount"]),
        "currency": "USD",
        "card_brand": case["network"],
        "refunded": Decimal(t["refunded"]),
    }
    async with c.sessions.begin() as s:
        svc = DisputeService(SqlDisputeRepository(s), SqlEvidenceRepository(s), SqlLedger(s), SystemClock())
        d = await svc.open(
            tenant_id="evalco",
            actor=REQUESTER,
            network=case["network"],
            reason_code=case["reason_code"],
            transaction_ref=ref,
            amount=Money.of(case["disputed_amount"], "USD"),
        )
        for kind in case["merchant_evidence"]:
            await svc.add_evidence(
                tenant_id="evalco",
                dispute_id=d.id,
                actor=REQUESTER,
                kind=kind,
                description=f"{kind} for {ref}",
                uri=None,
            )
        await s.execute(
            text("UPDATE disputes SET status='UNDER_REVIEW', respond_by=:r WHERE id=:i"),
            {"r": now + timedelta(days=case["days_to_deadline"]), "i": d.id},
        )

    gs = c.graph_services
    tools = RecordingTools(gateway)
    fixture = case.get("fixture")
    services = dataclasses.replace(
        gs,
        tools=tools,
        policy_store=FilteredStore(gs.policy_store, fixture) if fixture in STORE_FIXTURES else gs.policy_store,
        resolution_drafter=DrafterDouble(gs.resolution_drafter, fixture)
        if fixture and fixture.startswith("drafter")
        else gs.resolution_drafter,
    )
    graph = c.resolution_graph

    def ctx(user: str, role: str) -> Any:
        return dataclasses.replace(
            c.graph_context(tenant_id="evalco", user_id=user, role=role, request_id=f"eval-{case['id']}"),
            services=services,
        )

    async def trace(inp: Any, who: Any, tid: str) -> list[str]:
        path: list[str] = []
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": c.limits.recursion_limit}
        async for chunk in graph.astream(inp, cfg, context=who, stream_mode="updates"):
            path += [n for n in chunk if n != "__interrupt__"]
        return path

    t0 = time.perf_counter()
    tid = await gs.threads.start(tenant_id="evalco", dispute_id=d.id, graph="resolution", started_by=REQUESTER)
    inp = {"dispute_id": str(d.id), "requested_by": REQUESTER, "instructions": case.get("instructions") or ""}
    path = await trace(inp, ctx(REQUESTER, "analyst"), tid)
    snap = await graph.aget_state({"configurable": {"thread_id": tid}})
    v = snap.values
    draft, action = v.get("draft"), v.get("action") or {}
    actual = {
        "outcome": v.get("outcome") or ("AWAITING_APPROVAL" if snap.interrupts else None),
        "resolution": draft["resolution"] if draft else None,
        "amount": draft["amount"] if draft else None,
        "required_role": action.get("required_role"),
        "priority": action.get("priority"),
        "draft": draft,
        "reason": v.get("outcome_reason"),
    }
    evidence = list(v.get("evidence") or [])
    rec: dict[str, Any] = {
        "id": case["id"],
        "source": case.get("source"),
        "fixture": fixture,
        "family": FAMILY[(case["network"], case["reason_code"])],
        "expected": case["expected"],
        "actual": actual,
        "path": path,
        "evidence": evidence,
        "tool_calls": tools.calls,
        "path_after": None,
        "after": None,
    }
    if snap.interrupts and v.get("approval"):
        approvals = ApprovalService(gs.approvals, gs.audit, gs.clock)
        ap = await approvals.get("evalco", uuid.UUID(v["approval"]["approval_id"]))
        decided = await approvals.decide(
            tenant_id="evalco",
            approval_id=ap.id,
            user_id=APPROVER,
            role="admin",
            decision="approve",
            note=None,
            seen_summary_hash=summary_hash(ap.summary),
            request_id=f"eval-{case['id']}",
        )
        rec["path_after"] = await trace(Command(resume=resume_value(decided)), ctx(APPROVER, "admin"), tid)
        rec["after"] = {"outcome": (await graph.aget_state({"configurable": {"thread_id": tid}})).values.get("outcome")}
    rec["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    chunk_ids = [e["id"].removeprefix("policy:") for e in evidence if e["source"] == "policy"]
    async with c.sessions() as s:
        rows = (
            (
                await s.execute(
                    text("SELECT DISTINCT tenant_id FROM policy_chunks WHERE id = ANY(CAST(:ids AS uuid[]))"),
                    {"ids": chunk_ids},
                )
            ).all()
            if chunk_ids
            else []
        )
    rec["foreign_evidence"] = sum(1 for (tenant,) in rows if tenant not in (None, "evalco"))
    return rec


# --- scoring and comparison ---------------------------------------------------------------------------------------


def aggregate(records: list[dict[str, Any]]) -> dict[str, float]:
    from evals.scorers import abstain, generation, ops, retrieval, tools, trajectory

    per: dict[str, list[float]] = {}
    for r in records:
        r["scores"] = {**generation.score(r), **retrieval.score(r), **trajectory.score(r), **tools.score(r)}
        for k, val in r["scores"].items():
            per.setdefault(k, []).append(val)
    out = {k: round(sum(vs) / len(vs), 4) for k, vs in per.items() if k != "cross_tenant_evidence"}
    out["cross_tenant_evidence"] = float(sum(per.get("cross_tenant_evidence", [0.0])))
    out.update(abstain.aggregate(records))
    out.update(ops.aggregate(records))
    out["cases"] = float(len(records))
    return out


def compare(metrics: dict[str, float], thresholds: dict[str, Any], baseline: dict[str, float] | None) -> list[str]:
    problems = []
    for name, rule in thresholds["metrics"].items():
        value = metrics.get(name)
        if value is None:
            problems.append(f"{name}: missing")
        elif "min" in rule and value < rule["min"]:
            problems.append(f"{name} = {value} < min {rule['min']}")
        elif "max" in rule and value > rule["max"]:
            problems.append(f"{name} = {value} > max {rule['max']}")
    if baseline:
        tol = thresholds.get("regression_tolerance", 0.02)
        for name, rule in thresholds["metrics"].items():
            if name in baseline and name in metrics and "min" in rule and metrics[name] < baseline[name] - tol:
                problems.append(f"{name} regressed: {metrics[name]} vs baseline {baseline[name]} (tolerance {tol})")
    return problems


def report_md(metrics: dict[str, float], problems: list[str], records: list[dict[str, Any]], dataset: str) -> str:
    lines = [
        f"# Eval report — {dataset}",
        "",
        f"{int(metrics['cases'])} cases, drafter `{os.environ.get('APP_MODEL_PROVIDER', 'rules')}`",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in sorted(metrics.items())]
    lines += ["", "## Gate", "", *(f"- FAIL {p}" for p in problems)] if problems else ["", "## Gate", "", "- PASS"]
    misses = [r for r in records if any(v < 1 for k, v in r["scores"].items() if k != "cross_tenant_evidence")]
    if misses:
        lines += ["", "## Cases below 1.0", "", "| case | expected | actual | path |", "|---|---|---|---|"]
        for r in misses[:40]:
            e, a = r["expected"], r["actual"]
            lines.append(
                f"| {r['id']} | {e['outcome']} {e.get('resolution') or ''} {e.get('amount') or ''} | "
                f"{a['outcome']} {a.get('resolution') or ''} {a.get('amount') or ''} | {' > '.join(r['path'])} |"
            )
    return "\n".join(lines) + "\n"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/golden_v1.jsonl")
    ap.add_argument("--write-baseline", default=None)
    ap.add_argument("--baseline", default=None, help="baseline file to compare with (default: newest)")
    a = ap.parse_args()
    cases = [json.loads(line) for line in Path(a.dataset).read_text().splitlines() if line.strip()]
    prepare_database()
    from fastapi import FastAPI
    from mcp.client.client import Client

    from app.bootstrap.lifespan import lifespan
    from app.bootstrap.settings import get_settings
    from app.infrastructure.tools.gateway import McpToolGateway, TokenBucket
    from app.infrastructure.tools.mcp_client import McpConnection
    from mcp_servers import network_gateway, payments

    get_settings.cache_clear()
    import logging

    from app.bootstrap.observability import configure_logging

    configure_logging(logging.WARNING)  # node logs are not useful at eval volume
    app = FastAPI()
    async with lifespan(app, start_sweeper=False):
        c = app.state.container
        await seed(c)
        ledger = payments.PaymentsLedger()
        conns = {
            "payments": McpConnection("payments", lambda: Client(payments.create_server(ledger)), 5.0),
            "network-gateway": McpConnection(
                "network-gateway",
                lambda: Client(network_gateway.create_server(network_gateway.NetworkGatewayStore())),
                5.0,
            ),
        }
        gateway = McpToolGateway(
            connections=conns,
            approvals=c.graph_services.approvals,
            sessions=c.sessions,
            limiter=TokenBucket(1000, 1000),
            max_attempts=3,
            backoff_s=0.05,
        )
        records = [await run_case(c, gateway, ledger, case) for case in cases]
        for conn in conns.values():
            await conn.close()
    metrics = aggregate(records)
    thresholds = yaml.safe_load(Path("evals/thresholds.yaml").read_text())
    baselines = sorted(Path("evals/baselines").glob("*.json"), key=lambda p: p.stat().st_mtime)
    base_path = Path(a.baseline) if a.baseline else (baselines[-1] if baselines else None)
    baseline = json.loads(base_path.read_text())["metrics"] if base_path and not a.write_baseline else None
    problems = compare(metrics, thresholds, baseline)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    Path("evals/reports").mkdir(exist_ok=True)
    md = report_md(metrics, problems, records, a.dataset)
    Path(f"evals/reports/{stamp}.md").write_text(md)
    Path(f"evals/reports/{stamp}.json").write_text(json.dumps({"metrics": metrics, "records": records}, default=str))
    if a.write_baseline:
        Path(f"evals/baselines/{a.write_baseline}.json").write_text(
            json.dumps(
                {"name": a.write_baseline, "dataset": a.dataset, "created": stamp, "metrics": metrics},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    print(md)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
