"""T17.4 retrieval: the family's network-rule summary is among the gathered evidence; NAD also needs the merchant
policy; no evidence from another tenant (scope check is done against the DB by the runner)."""

from __future__ import annotations

from typing import Any

NETWORK_DOC = {
    "FRAUD": "card-absent fraud",
    "NOT_RECEIVED": "merchandise not received",
    "NOT_AS_DESCRIBED": "not as described",
    "PROCESSING_ERROR": "processing errors",
}


def score(rec: dict[str, Any]) -> dict[str, Any]:
    if (
        rec.get("fixture") in ("empty_policy_store", "network_docs_only")
        or rec["actual"]["outcome"] == "DEADLINE_PASSED"
    ):
        return {}
    titles = [e["title"].lower() for e in rec["evidence"] if e["source"] == "policy"]
    hit = any(NETWORK_DOC[rec["family"]] in t for t in titles)
    out = {"retrieval_network_rule_recall": float(hit), "cross_tenant_evidence": rec["foreign_evidence"]}
    if rec["family"] == "NOT_AS_DESCRIBED":
        out["retrieval_merchant_policy_recall"] = float(any("evalco" in t for t in titles))
    return out
