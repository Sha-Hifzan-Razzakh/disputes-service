"""Reducers for multi-writer state fields (T05.4). Pure, deterministic and order-independent.

`merge_evidence` is written by parallel branches (policy retrieval per query via `Send`, and ledger facts), so it
must give the same result whatever order LangGraph applies the writes in: it is commutative, associative and
idempotent (property-tested in tests/unit/test_reducers.py).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_EVIDENCE = 12  # policy passages (search hits)
MAX_RECORDS = 24  # ledger facts and merchant uploads: records, not search hits
RECORD_PREFIXES = ("ledger:", "merchant:")  # partition by id, so every copy of an id lands in the same partition


def merge_evidence(old: Sequence[Mapping[str, Any]] | None, new: Sequence[Mapping[str, Any]] | None) -> list[Any]:
    """Union by `id`, keeping the best copy of each id, sorted by (-score, id). Records (ledger, merchant) and
    policy passages are capped separately: records always score 1.0, so one shared cap let a case with many
    uploads push every network rule out and abstain for ever (review R6).

    "Best" is a total order on the whole item — highest score, then smallest canonical JSON — so the result never
    depends on which branch wrote first; per-partition top-k keeps the reducer associative."""
    best: dict[str, Mapping[str, Any]] = {}
    for item in (*(old or ()), *(new or ())):
        cur = best.get(item["id"])
        if cur is None or _rank(item) < _rank(cur):
            best[item["id"]] = item
    ranked = sorted(best.values(), key=lambda e: (-e["score"], e["id"]))
    records = [e for e in ranked if str(e["id"]).startswith(RECORD_PREFIXES)][:MAX_RECORDS]
    passages = [e for e in ranked if not str(e["id"]).startswith(RECORD_PREFIXES)][:MAX_EVIDENCE]
    return [dict(e) for e in sorted((*records, *passages), key=lambda e: (-e["score"], e["id"]))]


def _rank(item: Mapping[str, Any]) -> tuple[float, str]:
    return (-float(item["score"]), json.dumps(item, sort_keys=True, default=str))


class OutcomeAlreadySet(ValueError):
    pass


def write_once(old: str | None, new: str | None) -> str | None:
    """A terminal value is written exactly once per thread. Rewriting the same value is a no-op (safe replay);
    a different value means two terminal paths ran, which is a graph bug."""
    if not old:
        return new
    if not new or new == old:
        return old
    raise OutcomeAlreadySet(f"outcome already {old!r}, refusing {new!r}")
