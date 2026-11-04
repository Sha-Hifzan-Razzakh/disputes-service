"""The promptfoo suite (T17.9) runs only with a model key, so its generator and assertion are checked here offline:
the oracle's own answer must pass and the classic failures must fail with the right component."""

from __future__ import annotations

import json

import pytest

from evals.promptfoo.assert_draft import check
from evals.promptfoo.cases import generate_tests

TESTS = generate_tests()


def oracle_reply(v: dict) -> str:
    ledger = [i for i in v["evidence_ids"] if i.startswith("ledger:")]
    return json.dumps(
        {
            "resolution": v["expected_resolution"],
            "amount": v["expected_amount"],
            "rationale": "Per the cited evidence.",
            "citations": [] if v["expected_resolution"] == "REQUEST_INFO" else ledger[:1],
        }
    )


def test_suite_covers_hand_cases_and_every_red_team_probe() -> None:
    ids = {t["vars"]["case_id"] for t in TESTS}
    assert {f"RT-{i:02d}" for i in range(1, 9)} <= ids
    assert len([i for i in ids if i.startswith("G")]) >= 20
    for t in TESTS:
        assert "<analyst_note>" in t["vars"]["prompt"] and "EVIDENCE" in t["vars"]["prompt"]


@pytest.mark.parametrize("t", TESTS, ids=lambda t: t["vars"]["case_id"])
def test_oracle_answer_passes(t: dict) -> None:
    res = check(oracle_reply(t["vars"]), t["vars"])
    assert res["pass"], res["reason"]


def test_failures_are_named() -> None:
    v = next(t["vars"] for t in TESTS if t["vars"]["expected_resolution"] == "CONTEST")
    bad = {
        "not json": ("I think we should contest.", "schema"),
        "invented citation": (
            json.dumps({"resolution": "CONTEST", "amount": None, "rationale": "x", "citations": ["policy:made-up"]}),
            "citations_known",
        ),
        "uncited": (
            json.dumps({"resolution": "CONTEST", "amount": None, "rationale": "x", "citations": []}),
            "citation_present",
        ),
        "overpays": (
            json.dumps(
                {"resolution": "ACCEPT", "amount": "999.00", "rationale": "x", "citations": ["ledger:three_ds"]}
            ),
            "amount_rule",
        ),
    }
    for name, (reply, component) in bad.items():
        res = check(reply, v)
        assert not res["pass"] and component in res["reason"], (name, res["reason"])
