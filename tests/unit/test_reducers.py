"""Reducer laws (G3): parallel writers must not depend on arrival order."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.orchestration.reducers import MAX_EVIDENCE, OutcomeAlreadySet, merge_evidence, write_once

evidence = st.fixed_dictionaries(
    {
        "id": st.sampled_from([f"policy:c{i}" for i in range(8)] + [f"ledger:f{i}" for i in range(8)]),
        "source": st.sampled_from(["policy", "ledger", "merchant"]),
        "scope": st.sampled_from(["network", "tenant", None]),
        "kind": st.sampled_from(["network_rule", "merchant_policy", "three_ds_result"]),
        "title": st.text(max_size=8),
        "excerpt": st.text(max_size=12),
        "score": st.floats(min_value=0, max_value=1, allow_nan=False),
    }
)
batch = st.lists(evidence, max_size=10)


@settings(max_examples=300)
@given(batch, batch)
def test_merge_is_commutative(a: list[dict], b: list[dict]) -> None:
    assert merge_evidence(a, b) == merge_evidence(b, a)


@settings(max_examples=300)
@given(batch, batch, batch)
def test_merge_is_associative(a: list[dict], b: list[dict], c: list[dict]) -> None:
    assert merge_evidence(merge_evidence(a, b), c) == merge_evidence(a, merge_evidence(b, c))


@given(batch)
def test_merge_is_idempotent_unique_sorted_and_capped(a: list[dict]) -> None:
    once = merge_evidence([], a)
    assert merge_evidence(once, once) == once
    ids = [e["id"] for e in once]
    assert len(ids) == len(set(ids)) and len(once) <= MAX_EVIDENCE
    assert once == sorted(once, key=lambda e: (-e["score"], e["id"]))


def test_merge_keeps_highest_score_per_id() -> None:
    low = {
        "id": "policy:c1",
        "source": "policy",
        "scope": "network",
        "kind": "k",
        "title": "t",
        "excerpt": "x",
        "score": 0.1,
    }
    high = low | {"score": 0.9}
    assert merge_evidence([low], [high]) == [high]
    assert merge_evidence(None, None) == []


def test_write_once() -> None:
    assert write_once(None, "REFUNDED") == "REFUNDED"
    assert write_once("", "REFUNDED") == "REFUNDED"
    assert write_once("REFUNDED", "REFUNDED") == "REFUNDED"
    assert write_once("REFUNDED", None) == "REFUNDED"
    with pytest.raises(OutcomeAlreadySet):
        write_once("REFUNDED", "REJECTED")
