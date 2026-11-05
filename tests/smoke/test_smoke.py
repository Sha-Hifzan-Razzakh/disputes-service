"""What must work right after a deploy: health, readiness, metrics, one full approval flow across two
replicas, and tenant isolation. Uses CONTEST (representment) so repeated runs never exhaust a capture."""

from __future__ import annotations

import time


def test_health_readiness_and_metrics(a, b) -> None:
    for c in (a, b):
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/readyz").status_code == 200
    metrics = a.get("/metrics")
    assert metrics.status_code == 200 and "# TYPE" in metrics.text


def test_resolution_starts_on_one_replica_and_resumes_on_another(a, b, token) -> None:
    t0 = time.monotonic()
    d = a.post(
        "/v1/disputes",
        headers=token(),
        json={
            "network": "VISA",
            "reason_code": "10.4",
            "transaction_ref": "NW-1001",
            "amount": "189.00",
            "currency": "USD",
        },
    )
    assert d.status_code == 201, d.text
    did = d.json()["id"]
    for i, action in enumerate(["start_evidence", "submit_for_review"], 1):
        r = a.patch(f"/v1/disputes/{did}", headers=token(), json={"action": action, "expected_version": i})
        assert r.status_code == 200, r.text
    started = a.post(f"/v2/disputes/{did}/resolutions", headers=token(), json={})
    assert started.status_code == 202, started.text
    view = started.json()
    assert view["status"] == "awaiting_approval" and view["draft"]["resolution"] == "CONTEST"
    ap = b.get(f"/v2/approvals/{view['approval']['approval_id']}", headers=token("smoke-ben"))
    assert ap.status_code == 200, ap.text
    decided = b.post(
        f"/v2/approvals/{ap.json()['id']}/decision",
        headers=token("smoke-ben"),
        json={"decision": "approve", "summary_hash": ap.json()["summary_hash"]},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["resolution"]["outcome"] == "REPRESENTED"
    assert a.get(f"/v1/disputes/{did}", headers=token()).json()["status"] == "RESOLVED_CONTESTED"
    assert time.monotonic() - t0 < 30


def test_other_tenant_sees_nothing(a, token) -> None:
    d = a.post(
        "/v1/disputes",
        headers=token(),
        json={
            "network": "VISA",
            "reason_code": "13.1",
            "transaction_ref": "NW-1003",
            "amount": "89.50",
            "currency": "USD",
        },
    )
    assert d.status_code == 201
    assert a.get(f"/v1/disputes/{d.json()['id']}", headers=token(tenant="aurora")).status_code == 404
