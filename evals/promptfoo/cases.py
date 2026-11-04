"""promptfoo test generator (T17.9): the golden cases that reach drafting, rendered with the production prompt.

Each test carries the exact prompt `LlmResolutionDrafter` would send, plus what the checks need: the evidence ids
the model may cite, the disputed amount and the oracle's answer. Red-team probes (RT-*) are the same cases with an
adversarial analyst note, so the oracle answer must not move.

    npx promptfoo@latest eval -c evals/promptfoo/promptfooconfig.yaml      # needs ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.application.evidence import ledger_evidence, merchant_evidence
from app.infrastructure.drafting import resolution_prompt
from evals.oracle import FAMILY

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


def _state(c: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    t = dict(c["transaction"], ref=f"{c['id']}-TXN")
    uploads = [
        {"id": f"m{i}", "kind": k, "description": f"{k} uploaded by the merchant"}
        for i, k in enumerate(c["merchant_evidence"], 1)
    ]
    case = {
        "dispute": {
            "id": c["id"],
            "network": c["network"],
            "reason_code": c["reason_code"],
            "amount": c["disputed_amount"],
            "currency": c["currency"],
            "respond_by": (NOW + timedelta(days=c["days_to_deadline"])).isoformat(),
        },
        "transaction": t,
        "evidence": uploads,
        "reason_family": FAMILY[(c["network"], c["reason_code"])],
        "partial_pct": 50,
    }
    return case, ledger_evidence(t) + merchant_evidence(uploads)


def drafting_cases(path: Path = ROOT / "evals/golden_v1.jsonl") -> list[dict[str, Any]]:
    """Hand-labelled and red-team cases whose run reaches the drafter without a test double."""
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return [
        r
        for r in rows
        if r["source"] == "hand" or "red-team" in r["tags"]
        if r["expected"].get("resolution") and not r.get("fixture")
    ]


def generate_tests(config: Any = None) -> list[dict[str, Any]]:
    tests = []
    for c in drafting_cases():
        case, evidence = _state(c)
        exp = c["expected"]
        tests.append(
            {
                "description": f"{c['id']} {case['reason_family']} {c.get('note') or ''}".strip(),
                "vars": {
                    "prompt": resolution_prompt(case, evidence, [], c.get("instructions") or ""),
                    "case_id": c["id"],
                    "red_team": "red-team" in c["tags"],
                    "evidence_ids": [e["id"] for e in evidence],
                    "disputed": c["disputed_amount"],
                    "expected_resolution": exp["resolution"],
                    "expected_amount": exp.get("amount"),
                },
            }
        )
    return tests
