"""T25.6 / T25.7: online checks over real finished runs, and a production run captured as a golden candidate."""

from __future__ import annotations

from evals import online
from tests.graph.test_resolution_graph import dispute, start


async def _finished(client, auth, app, ref: str, code: str, amount: str, verdict: str) -> str:  # type: ignore[no-untyped-def]
    """Through the approvals API, as in production (the approvals row records the human decision)."""
    did = await dispute(client, auth, ref, code, amount)
    v = (await start(client, auth, did)).json()
    ap = (await client.get(f"/v2/approvals/{v['approval']['approval_id']}", headers=auth(user="ben"))).json()
    r = await client.post(
        f"/v2/approvals/{ap['id']}/decision",
        headers=auth(user="ben"),
        json={"decision": "approve" if verdict == "approved" else "reject", "summary_hash": ap["summary_hash"]},
    )
    assert r.status_code == 200, r.text
    return v["thread_id"]


async def test_scores_finished_runs(client, auth, app, tools, monkeypatch, tmp_path) -> None:
    from tests.graph.test_resolution_graph import sql

    since = sql("SELECT now()")[0][0]  # database clock: only the runs below count
    await _finished(client, auth, app, "NW-1001", "10.4", "189.00", "approved")  # REPRESENTED
    await _finished(client, auth, app, "NW-1003", "13.1", "89.50", "rejected")  # REJECTED
    monkeypatch.setattr(online, "THRESHOLDS", _thresholds(tmp_path))
    report = await online.score(app.state.container, since=since)
    s = report["scores"]
    assert report["runs"] == 2 and report["verdict"] in ("ok", "drift")
    assert s["grounded"] == 1.0 and s["execution_success"] == 1.0 and s["approval_acceptance"] == 0.5
    assert report["below"] == {"approval_acceptance": 0.5}  # humans rejected half: drift is reported


def _thresholds(d):  # type: ignore[no-untyped-def]
    p = d / "t.yaml"
    p.write_text(
        "min_runs: 1\nchecks: {grounded: 1.0, verified_first_draft: 0.5, approval_acceptance: 0.8,"
        " execution_success: 0.99, non_abstention: 0.5}\n"
    )
    return p


async def test_capture_turns_a_run_into_an_oracle_labelled_candidate(client, auth, app, tools) -> None:
    tid = await _finished(client, auth, app, "NW-1001", "10.4", "189.00", "approved")
    case = await online.capture(app.state.container, tid)
    assert case["expected"]["outcome"] == "AWAITING_APPROVAL" and case["expected"]["resolution"] == "CONTEST"
    assert case["production"]["resolution"] == "CONTEST" and case["note"] == "agrees with the oracle"
    assert "NW-1001" not in str(case) and case["transaction"]["three_ds_authenticated"] is True
