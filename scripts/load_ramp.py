"""Load ramp against a running service (T16.11/T16.12): starts resolutions at increasing concurrency and reports
latency percentiles and status codes per step. Each request uses its own fresh dispute.

    uv run python scripts/load_ramp.py --base http://localhost:8000 --steps 1,4,8,16,32 --per-step 32
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from collections import Counter

import httpx
import jwt

SECRET = "dev-only-secret-change-me-in-every-env"


def headers(user: str = "ana") -> dict[str, str]:
    tok = jwt.encode({"sub": user, "tenant": "northwind", "role": "analyst"}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


async def make_dispute(c: httpx.AsyncClient) -> str:
    d = (
        await c.post(
            "/v1/disputes",
            headers=headers(),
            json={
                "network": "VISA",
                "reason_code": "10.4",
                "transaction_ref": "NW-1001",
                "amount": "1.00",
                "currency": "USD",
            },
        )
    ).json()
    for i, a in enumerate(["start_evidence", "submit_for_review"], 1):
        await c.patch(f"/v1/disputes/{d['id']}", headers=headers(), json={"action": a, "expected_version": i})
    return str(d["id"])


async def one(c: httpx.AsyncClient, did: str) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = await c.post(f"/v2/disputes/{did}/resolutions", headers=headers(), json={})
    return r.status_code, (time.perf_counter() - t0) * 1000


async def step(c: httpx.AsyncClient, conc: int, n: int) -> dict[str, object]:
    disputes = [await make_dispute(c) for _ in range(n)]
    sem = asyncio.Semaphore(conc)

    async def guarded(did: str) -> tuple[int, float]:
        async with sem:
            return await one(c, did)

    t0 = time.perf_counter()
    res = await asyncio.gather(*(guarded(d) for d in disputes))
    wall = time.perf_counter() - t0
    ok = sorted(ms for code, ms in res if code < 400)
    q = statistics.quantiles(ok, n=20) if len(ok) >= 2 else [ok[0] if ok else 0] * 19
    return {
        "concurrency": conc,
        "requests": n,
        "codes": dict(Counter(code for code, _ in res)),
        "p50_ms": round(statistics.median(ok), 1) if ok else None,
        "p95_ms": round(q[18], 1) if ok else None,
        "max_ms": round(max(ok), 1) if ok else None,
        "throughput_rps": round(n / wall, 1),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--steps", default="1,4,8,16,32")
    ap.add_argument("--per-step", type=int, default=32)
    a = ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base, timeout=60, limits=httpx.Limits(max_connections=100)) as c:
        print("| concurrency | requests | status codes | p50 ms | p95 ms | max ms | throughput rps |")
        print("|---|---|---|---|---|---|---|")
        for conc in [int(x) for x in a.steps.split(",")]:
            r = await step(c, conc, a.per_step)
            print(
                f"| {r['concurrency']} | {r['requests']} | {r['codes']} | {r['p50_ms']} | {r['p95_ms']} | "
                f"{r['max_ms']} | {r['throughput_rps']} |",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
