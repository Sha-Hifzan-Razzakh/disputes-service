"""Stage 13 exit: a failed run is diagnosable from telemetry alone — spans per node with thread_id, error logs with
node and thread, request ids, metrics — and nothing sensitive reaches the logs."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from tests.graph.fakes import FailOnce
from tests.graph.test_resolution_graph import ctx, dispute, start


def log_lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    out = []
    for line in capsys.readouterr().out.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


async def test_failed_run_is_traceable_from_spans_and_logs(client, auth, app, mcp_tools, capsys) -> None:
    exporter = app.state.span_exporter
    exporter.clear()
    c = app.state.container
    original = c.graph_services
    did = await dispute(client, auth, "NW-1003", "13.1", "89.50")
    capsys.readouterr()
    c.graph_services = dataclasses.replace(
        original, disputes=FailOnce(original.disputes, "advance", RuntimeError("ledger write failed"), times=99)
    )
    try:
        v = (await start(client, auth, did)).json()
        approval = (await client.get(f"/v2/approvals/{v['approval']['approval_id']}", headers=auth(user="ben"))).json()
        with pytest.raises(RuntimeError):
            await client.post(
                f"/v2/approvals/{approval['id']}/decision",
                headers=auth(user="ben") | {"X-Request-ID": "diag-1"},
                json={"decision": "approve", "summary_hash": approval["summary_hash"]},
            )
    finally:
        c.graph_services = original
    tid = v["thread_id"]
    spans = exporter.get_finished_spans()
    node_spans = [s for s in spans if s.name.startswith("resolution.") and s.attributes.get("thread_id") == tid]
    names = {s.name for s in node_spans}
    assert {"resolution.load_case", "resolution.execute", "resolution.apply_resolution"} <= names
    failed = [s for s in node_spans if s.name == "resolution.apply_resolution"]
    assert failed and not failed[-1].status.is_ok  # error recorded on the span
    assert any(s.name == "tool payments.issue_refund" for s in spans)
    assert any(s.name == "db.transaction" for s in spans)  # DB work is traced too
    logs = log_lines(capsys)
    errors = [e for e in logs if e.get("event") == "node.error"]
    assert errors and errors[-1]["node"] == "apply_resolution" and errors[-1]["thread_id"] == tid
    assert errors[-1]["request_id"] == "diag-1" and len(errors[-1]["trace_id"]) == 32
    assert (await client.get(f"/admin/threads/{tid}/history", headers=auth(role="admin"))).json()["checkpoints"]
    # recover so later tests see a clean dispute
    out = await c.resolution_runner().recover(tid, ctx(app))
    assert out["outcome"] == "REFUNDED"


async def test_metrics_endpoint_exposes_graph_tool_and_outcome_series(client, auth, mcp_tools) -> None:
    did = await dispute(client, auth, "NW-1005", "13.3", "75.00")
    await start(client, auth, did)
    text = (await client.get("/metrics")).text
    for series in (
        "graph_node_duration_milliseconds_bucket",
        "resolution_outcomes_total",
        "tool_calls_total",
        'node="gather_evidence"',
        'outcome="INFO_REQUESTED"',
    ):
        assert series in text, series


async def test_logs_redact_sensitive_fields_and_patterns(client, auth, capsys) -> None:
    import structlog

    capsys.readouterr()
    structlog.get_logger().info(
        "probe",
        instructions="card 4111 1111 1111 1111",
        question="mail me at a@b.co",
        detail={"note": "secret", "msg": "pan 4111111111111111 and x@y.org"},
    )
    entry = next(e for e in log_lines(capsys) if e.get("event") == "probe")
    assert entry["instructions"] == "[REDACTED]" and entry["question"] == "[REDACTED]"
    assert entry["detail"] == {"note": "[REDACTED]", "msg": "pan [PAN] and [EMAIL]"}


def test_r15_exception_logs_carry_type_message_and_frames_but_no_locals(capsys) -> None:
    import json

    import structlog

    from app.bootstrap.observability import configure_logging

    configure_logging()
    card = "4111 1111 1111 1111"
    try:
        secret_local = card  # noqa: F841 - must not appear: frame locals are not rendered
        raise ValueError(f"refund failed for {card}")
    except ValueError:
        structlog.get_logger().exception("boom")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    exc = line["exception"][0]
    assert exc["exc_type"] == "ValueError" and exc["frames"] and "[PAN]" in exc["exc_value"]
    assert card not in json.dumps(line) and "secret_local" not in json.dumps(line)
