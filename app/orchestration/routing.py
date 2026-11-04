"""Routers: pure functions of state with labelled path maps (docs/graph_design.md). Decisions that need the clock,
I/O or a human are made in nodes (load_case, approve) and returned as Command."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

AfterGrade = Literal["sufficient", "retry", "exhausted"]
AfterEvidence = Literal["sufficient", "insufficient"]
AfterVerify = Literal["passed", "refine", "failed"]
ApprovalRoute = Literal["request_info", "approval"]
AfterExecute = Literal["succeeded", "failed"]


def route_after_grade(state: Mapping[str, Any]) -> AfterGrade:
    g = state["evidence_grade"]
    if g["sufficient"]:
        return "sufficient"
    return "retry" if g["can_retry"] else "exhausted"


def route_after_evidence(state: Mapping[str, Any]) -> AfterEvidence:
    return "sufficient" if state["evidence_grade"]["sufficient"] else "insufficient"


def route_after_verify(state: Mapping[str, Any]) -> AfterVerify:
    v = state["verification"]
    if v["passed"]:
        return "passed"
    return "refine" if v["can_refine"] else "failed"


def route_approval(state: Mapping[str, Any]) -> ApprovalRoute:
    return "request_info" if state["action"]["kind"] == "request_info" else "approval"


def route_after_execute(state: Mapping[str, Any]) -> AfterExecute:
    return "succeeded" if state["execution"]["status"] == "succeeded" else "failed"
