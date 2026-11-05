"""T17.8 tools: after approval, the right tool ran once with the right arguments and the run ended as expected."""

from __future__ import annotations

from typing import Any


def score(rec: dict[str, Any]) -> dict[str, Any]:
    after = rec["expected"].get("after_approval")
    if not after or rec.get("after") is None:
        return {}
    calls = rec["tool_calls"]
    ok = len(calls) == 1 and calls[0]["tool"] == after["tool"]
    if ok and after["tool"] == "payments.issue_refund":
        ok = calls[0]["amount"] == rec["expected"]["amount"]
    if ok and after["tool"] == "network.submit_representment":
        ok = bool(calls[0]["evidence_ids"]) and set(calls[0]["evidence_ids"]) <= {e["id"] for e in rec["evidence"]}
    return {
        "tool_correct": float(ok),
        "post_approval_outcome_match": float(rec["after"]["outcome"] == after["outcome"]),
    }
