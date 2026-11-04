"""Mirror the golden set into a LangSmith dataset (T17.3). Optional: does nothing without LANGSMITH_API_KEY.

    LANGSMITH_API_KEY=... uv run python -m evals.langsmith_sync [--dataset evals/golden_v1.jsonl]

Idempotent: examples are keyed by case id in metadata; a case whose content changed is updated, new ones are added,
removed ones are deleted. The JSONL in git stays the source of truth.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def split(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = {k: v for k, v in case.items() if k != "expected"}
    return inputs, case["expected"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/golden_v1.jsonl")
    a = ap.parse_args()
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not set; skipping (the JSONL in git is the source of truth)")
        return 0
    from langsmith import Client

    path = Path(a.dataset)
    name = f"disputes-{path.stem.replace('_', '-')}"
    cases = {c["id"]: c for c in map(json.loads, path.read_text().splitlines())}
    client = Client()
    ds = (
        client.read_dataset(dataset_name=name)
        if client.has_dataset(dataset_name=name)
        else client.create_dataset(name, description=f"Golden cases from {path} (disputes-service)")
    )
    existing = {ex.metadata.get("case_id"): ex for ex in client.list_examples(dataset_id=ds.id) if ex.metadata}
    added = updated = 0
    for cid, case in cases.items():
        inputs, outputs = split(case)
        meta = {"case_id": cid, "tags": case["tags"], "source": case["source"]}
        if cid not in existing:
            client.create_example(inputs=inputs, outputs=outputs, metadata=meta, dataset_id=ds.id)
            added += 1
        elif existing[cid].inputs != inputs or existing[cid].outputs != outputs:
            client.update_example(existing[cid].id, inputs=inputs, outputs=outputs, metadata=meta)
            updated += 1
    gone = [ex.id for cid, ex in existing.items() if cid not in cases]
    for ex_id in gone:
        client.delete_example(ex_id)
    print(f"{name}: {added} added, {updated} updated, {len(gone)} removed, {len(cases)} total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
