"""Rollback drill (T19.8): prove the previous release can serve a database the new release has written to.

Phase `write` (run with the NEW code): create a dispute, ask the v2 assist graph a question (checkpoint with
v2 state keys), start a resolution that pauses for approval.
Phase `read` (run with the PREVIOUS release's code against the same database): ask the same assist thread a
follow-up and read the dispute. The v2 tables and the paused resolution thread are left alone by v1.
Phase `forward` (NEW code again): the paused resolution is still decidable after the round trip.

    uv run python scripts/rollback_drill.py write   --base http://127.0.0.1:8010
    uv run python scripts/rollback_drill.py read    --base http://127.0.0.1:8011   # previous image/commit
    uv run python scripts/rollback_drill.py forward --base http://127.0.0.1:8010
State between phases is kept in a small JSON file (--state).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import jwt

SECRET = os.environ.get("APP_JWT_SECRET", "dev-only-secret-change-me-in-every-env")


def h(user: str = "ana", role: str = "analyst") -> dict[str, str]:
    tok = jwt.encode({"sub": user, "tenant": "northwind", "role": role}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def check(r: httpx.Response, *ok: int) -> dict:
    if r.status_code not in ok:
        sys.exit(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["write", "read", "forward"])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--state", default="rollback_drill.json")
    a = ap.parse_args()
    state = Path(a.state)
    with httpx.Client(base_url=a.base, timeout=30) as c:
        if a.phase == "write":
            d = check(
                c.post(
                    "/v1/disputes",
                    headers=h(),
                    json={
                        "network": "VISA",
                        "reason_code": "13.1",
                        "transaction_ref": "NW-1003",
                        "amount": "89.50",
                        "currency": "USD",
                    },
                ),
                201,
            )
            for i, act in enumerate(["start_evidence", "submit_for_review"], 1):
                check(c.patch(f"/v1/disputes/{d['id']}", headers=h(), json={"action": act, "expected_version": i}), 200)
            check(c.post(f"/v1/disputes/{d['id']}/assist", headers=h(), json={"question": "What evidence helps?"}), 200)
            d2 = check(
                c.post(
                    "/v1/disputes",
                    headers=h(),
                    json={
                        "network": "VISA",
                        "reason_code": "10.4",
                        "transaction_ref": "NW-1001",
                        "amount": "189.00",
                        "currency": "USD",
                    },
                ),
                201,
            )
            for i, act in enumerate(["start_evidence", "submit_for_review"], 1):
                check(
                    c.patch(f"/v1/disputes/{d2['id']}", headers=h(), json={"action": act, "expected_version": i}), 200
                )
            res = check(c.post(f"/v2/disputes/{d2['id']}/resolutions", headers=h(), json={}), 202)
            state.write_text(
                json.dumps(
                    {
                        "assist_dispute": d["id"],
                        "resolution_dispute": d2["id"],
                        "approval_id": res["approval"]["approval_id"],
                        "thread_id": res["thread_id"],
                    }
                )
            )
            print("write: assist answered (v2 graph), resolution paused for approval", res["thread_id"])
        elif a.phase == "read":
            s = json.loads(state.read_text())
            r = check(
                c.post(
                    f"/v1/disputes/{s['assist_dispute']}/assist",
                    headers=h(),
                    json={"question": "And if the customer says it never arrived?"},
                ),
                200,
            )
            check(c.get(f"/v1/disputes/{s['resolution_dispute']}", headers=h()), 200)
            print("read: previous release answered a follow-up on the v2 thread:", sorted(r)[:6])
        else:
            s = json.loads(state.read_text())
            ap_ = check(c.get(f"/v2/approvals/{s['approval_id']}", headers=h("ben")), 200)
            r = check(
                c.post(
                    f"/v2/approvals/{s['approval_id']}/decision",
                    headers=h("ben"),
                    json={"decision": "approve", "summary_hash": ap_["summary_hash"]},
                ),
                200,
            )
            print("forward: paused resolution decided after the round trip ->", r["resolution"]["outcome"])


if __name__ == "__main__":
    main()
