"""T17.5 generation: first-stop outcome, resolution, amount, approval route; drafts cite only gathered evidence.
(LLM faithfulness via Ragas runs only when a model key is configured: evals/ragas_faithfulness.py.)"""

from __future__ import annotations

from typing import Any


def score(rec: dict[str, Any]) -> dict[str, Any]:
    exp, act = rec["expected"], rec["actual"]
    out: dict[str, Any] = {"outcome_match": float(exp["outcome"] == act["outcome"])}
    if exp["outcome"] in ("AWAITING_APPROVAL", "INFO_REQUESTED"):
        out["resolution_match"] = float(exp.get("resolution") == act.get("resolution"))
    if exp["outcome"] == "AWAITING_APPROVAL":
        out["amount_match"] = float(exp.get("amount") == act.get("amount"))
        out["route_match"] = float(
            (exp.get("required_role"), exp.get("priority")) == (act.get("required_role"), act.get("priority"))
        )
    if act.get("draft"):
        cites = act["draft"]["citations"]
        ids = {e["id"] for e in rec["evidence"]}
        out["grounded"] = float(
            all(c in ids for c in cites) and (bool(cites) or act["draft"]["resolution"] == "REQUEST_INFO")
        )
    return out
