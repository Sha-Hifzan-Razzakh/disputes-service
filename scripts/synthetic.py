"""Synthetic monitor (T25.4): one read-only request path every few minutes from inside the cluster (CronJob
`synthetic`). Exit code 1 on any failure or a slow answer; the failed Job raises SyntheticCheckFailing.

    APP_JWT_SECRET=... python scripts/synthetic.py --base http://api
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx
import jwt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--tenant", default="northwind")
    ap.add_argument("--budget-ms", type=float, default=1500)
    a = ap.parse_args()
    tok = jwt.encode({"sub": "synthetic", "tenant": a.tenant, "role": "analyst"}, os.environ["APP_JWT_SECRET"], "HS256")
    checks = [("/healthz", None), ("/readyz", None), ("/v1/disputes?limit=1", {"Authorization": f"Bearer {tok}"})]
    results, ok = [], True
    with httpx.Client(base_url=a.base, timeout=5) as c:
        for path, headers in checks:
            t0 = time.perf_counter()
            status: int | str
            try:
                status = c.get(path, headers=headers).status_code
            except httpx.HTTPError as e:
                status = f"error: {type(e).__name__}"
            ms = round((time.perf_counter() - t0) * 1000, 1)
            good = status == 200 and ms <= a.budget_ms
            ok &= good
            results.append({"path": path, "status": status, "ms": ms, "ok": good})
    print(json.dumps({"event": "synthetic", "ok": ok, "checks": results}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
