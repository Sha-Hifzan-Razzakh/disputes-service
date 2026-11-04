"""Every router branch (stage 9 exit: 'every router branch tested')."""

import pytest

from app.orchestration import routing as R


@pytest.mark.parametrize(
    ("grade", "want"),
    [
        ({"sufficient": True, "can_retry": True}, "sufficient"),
        ({"sufficient": False, "can_retry": True}, "retry"),
        ({"sufficient": False, "can_retry": False}, "exhausted"),
    ],
)
def test_after_grade(grade: dict, want: str) -> None:
    assert R.route_after_grade({"evidence_grade": grade}) == want


def test_after_evidence() -> None:
    assert R.route_after_evidence({"evidence_grade": {"sufficient": True}}) == "sufficient"
    assert R.route_after_evidence({"evidence_grade": {"sufficient": False}}) == "insufficient"


@pytest.mark.parametrize(
    ("v", "want"),
    [
        ({"passed": True, "can_refine": False}, "passed"),
        ({"passed": False, "can_refine": True}, "refine"),
        ({"passed": False, "can_refine": False}, "failed"),
    ],
)
def test_after_verify(v: dict, want: str) -> None:
    assert R.route_after_verify({"verification": v}) == want


def test_approval_route_and_after_execute() -> None:
    assert R.route_approval({"action": {"kind": "request_info"}}) == "request_info"
    assert R.route_approval({"action": {"kind": "refund"}}) == "approval"
    assert R.route_after_execute({"execution": {"status": "succeeded"}}) == "succeeded"
    assert R.route_after_execute({"execution": {"status": "unknown"}}) == "failed"
