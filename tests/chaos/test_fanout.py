"""T16.9: a 50-way Send fan-out into one reducer: every branch runs, concurrency stays within max_concurrency,
and the merged evidence is identical whatever order branches finish in."""

from __future__ import annotations

import asyncio
import dataclasses
import random
from typing import Any

from app.application.ports import PolicyHit
from app.orchestration import evidence as E
from tests.fakes import make_ctx, make_services


class Tracking:
    def __init__(self) -> None:
        self.calls, self.active, self.peak = 0, 0, 0

    async def search(self, *, tenant_id: str, query: str, k: int) -> list[PolicyHit]:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(random.uniform(0, 0.01))
        self.active -= 1
        n = int(query.split()[-1])
        return [PolicyHit(f"c{n:02d}", "Network rules", f"text {n}", round(0.1 + n / 100, 4), "network")]


async def _run(monkeypatch: Any) -> tuple[list[dict[str, Any]], Tracking]:
    monkeypatch.setattr(
        E, "plan_queries", lambda s: {"queries": [f"q {i}" for i in range(50)], "retrieval_attempts": 9}
    )
    graph = E.build_evidence_graph().compile()
    store = Tracking()
    ctx = make_ctx(dataclasses.replace(make_services(), policy_store=store))
    inp = {
        "network": "VISA",
        "reason_code": "10.4",
        "reason_family": "FRAUD",
        "question": "",
        "facts": {
            "transaction": {
                "ref": "R",
                "amount": "1.00",
                "three_ds_authenticated": True,
                "avs_result": "Y",
                "cvv_result": "M",
                "delivery_status": None,
                "delivery_tracking": None,
                "refunded": "0",
            },
            "evidence": [],
        },
    }
    out = await graph.ainvoke(inp, {"max_concurrency": 4}, context=ctx)
    return out["evidence"], store


async def test_fifty_branches_merge_deterministically_within_the_concurrency_cap(monkeypatch) -> None:
    first, store = await _run(monkeypatch)
    second, _ = await _run(monkeypatch)
    assert store.calls == 50 and store.peak <= 4
    policy = [e for e in first if e["source"] == "policy"]
    assert first == second and len(first) == 16  # 4 ledger records + policy passages capped at 12 (review R6)
    assert [e["id"] for e in policy] == [f"policy:c{i}" for i in range(49, 37, -1)]  # the 12 best of 50
    assert [e["id"] for e in first[:5]] == [
        "ledger:avs_cvv",
        "ledger:delivery",
        "ledger:refunds",
        "ledger:three_ds",
        "policy:c49",
    ]
