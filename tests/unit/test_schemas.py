"""API DTOs reject bad input before any work happens (T14.7)."""

import pytest
from pydantic import ValidationError

from app.api.schemas import OpenDisputeIn
from app.api.schemas_v2 import DecisionIn, StartResolutionIn


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (StartResolutionIn, {"instructions": "x" * 1001}),
        (DecisionIn, {"decision": "maybe", "summary_hash": "a" * 64}),
        (DecisionIn, {"decision": "approve", "summary_hash": "short"}),
        (DecisionIn, {"decision": "approve", "summary_hash": "a" * 64, "note": "n" * 1001}),
        (
            OpenDisputeIn,
            {"network": "AMEX", "reason_code": "10.4", "transaction_ref": "NW-1", "amount": "1", "currency": "USD"},
        ),
        (
            OpenDisputeIn,
            {"network": "VISA", "reason_code": "10.4", "transaction_ref": "NW-1", "amount": "-1", "currency": "USD"},
        ),
        (
            OpenDisputeIn,
            {"network": "VISA", "reason_code": "10.4", "transaction_ref": "NW-1", "amount": "1.001", "currency": "USD"},
        ),
        (
            OpenDisputeIn,
            {"network": "VISA", "reason_code": "10.4", "transaction_ref": "NW-1", "amount": "1", "currency": "usd"},
        ),
    ],
)
def test_rejected(model: type, data: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_accepted_defaults() -> None:
    assert StartResolutionIn().instructions == ""
    assert DecisionIn(decision="reject", summary_hash="a" * 64).note is None


def test_db_session_commits_before_the_response_is_sent() -> None:
    """Read-your-writes across workers: the request's transaction must be committed before the client sees the
    response (found by the two-worker load ramp; docs/capacity.md)."""
    from app.api.deps import Session

    dep = Session.__metadata__[0]
    assert dep.scope == "function"
