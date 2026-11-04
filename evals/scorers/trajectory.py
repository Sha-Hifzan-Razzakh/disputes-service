"""T17.7 trajectory: the node path to the first stop (and after approval) equals the labelled path."""

from __future__ import annotations

from typing import Any


def score(rec: dict[str, Any]) -> dict[str, Any]:
    out = {"trajectory_exact": float(rec["path"] == rec["expected"]["path"])}
    after = rec["expected"].get("after_approval")
    if after and rec.get("path_after") is not None:
        out["trajectory_after_approval_exact"] = float(rec["path_after"] == after["path"])
    return out
