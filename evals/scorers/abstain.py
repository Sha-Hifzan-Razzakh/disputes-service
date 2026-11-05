"""T17.6 abstention: computed over the whole run (precision / recall of ABSTAINED_* outcomes)."""

from __future__ import annotations

from typing import Any


def aggregate(records: list[dict[str, Any]]) -> dict[str, float]:
    def abst(o: str | None) -> bool:
        return bool(o) and str(o).startswith("ABSTAINED_")

    tp = sum(abst(r["expected"]["outcome"]) and abst(r["actual"]["outcome"]) for r in records)
    fp = sum(not abst(r["expected"]["outcome"]) and abst(r["actual"]["outcome"]) for r in records)
    fn = sum(abst(r["expected"]["outcome"]) and not abst(r["actual"]["outcome"]) for r in records)
    return {
        "abstain_precision": tp / (tp + fp) if tp + fp else 1.0,
        "abstain_recall": tp / (tp + fn) if tp + fn else 1.0,
    }
