"""State contracts (G3, T05.3): one owner per field or a declared multi-writer rule; no identity in state."""

from __future__ import annotations

import operator
import typing

import pytest

from app.orchestration import state as S
from app.orchestration.reducers import merge_evidence, write_once

SCHEMAS = {"resolution": S.ResolutionState, "assist": S.AssistStateV2, "evidence": S.EvidenceState}


def _owners_of(graph: str) -> dict[str, set[str]]:
    by_field: dict[str, set[str]] = {}
    for owner, fields in S.OWNERS[graph].items():
        for f in fields:
            by_field.setdefault(f, set()).add(owner)
    return by_field


@pytest.mark.parametrize("graph", SCHEMAS)
def test_every_field_has_an_owner_and_every_owner_names_real_fields(graph: str) -> None:
    fields = set(typing.get_type_hints(SCHEMAS[graph]))
    owned = _owners_of(graph)
    assert fields == set(owned), f"unowned: {fields - set(owned)}, unknown: {set(owned) - fields}"


@pytest.mark.parametrize("graph", SCHEMAS)
def test_multi_writer_fields_are_declared_and_reduced(graph: str) -> None:
    hints = typing.get_type_hints(SCHEMAS[graph], include_extras=True)
    for field, owners in _owners_of(graph).items():
        if len(owners) > 1:
            assert field in S.MULTI_WRITER, f"{field} has owners {owners} but no multi-writer rule"
    reducers = {f: h.__metadata__[0] for f, h in hints.items() if hasattr(h, "__metadata__")}
    if graph == "evidence":
        assert reducers == {"evidence": merge_evidence}  # parallel Send branches merge
    elif graph == "resolution":
        assert reducers == {"outcome": write_once, "outcome_reason": write_once}
    else:
        assert reducers == {"history": operator.add}  # v1 threads keep counting turns; evidence replaces per turn


def test_no_identity_or_secrets_in_any_state() -> None:
    for schema in (*SCHEMAS.values(), S.ResolutionIn, S.ResolutionOut):
        assert not set(typing.get_type_hints(schema)) & S.FORBIDDEN_KEYS


def test_io_schemas_are_subsets_of_state() -> None:
    state = set(typing.get_type_hints(S.ResolutionState))
    assert set(typing.get_type_hints(S.ResolutionIn)) <= state
    assert set(typing.get_type_hints(S.ResolutionOut)) <= state


def test_sensitive_fields_exist() -> None:
    every = set().union(*(typing.get_type_hints(s) for s in SCHEMAS.values()))
    assert set(S.SENSITIVE) <= every
