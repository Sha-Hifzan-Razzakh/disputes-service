"""Domain rules: evidence sufficiency (D2), verification R1-R6 (D4), partial amounts, approval routing (D6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain import approval_policy
from app.domain.dispute import DisputeStatus, path_to
from app.domain.errors import InvalidTransition
from app.domain.money import Money
from app.domain.reason_codes import ReasonFamily, Resolution, lookup
from app.domain.rules import grade_evidence, partial_amount, verify_draft

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)
USD = lambda a: Money.of(a, "USD")  # noqa: E731


def ev(id_: str, source: str = "policy", scope: str | None = "network", kind: str = "network_rule", score: float = 0.5):
    return {"id": id_, "source": source, "scope": scope, "kind": kind, "title": "t", "excerpt": "x", "score": score}


LEDGER = [
    ev(f"ledger:{f}", "ledger", None, k, 1.0)
    for f, k in [
        ("three_ds", "three_ds_result"),
        ("avs_cvv", "avs_cvv_result"),
        ("delivery", "delivery_proof"),
        ("refunds", "refund_none"),
    ]
]


class TestGrade:
    def test_fraud_sufficient_with_ledger_facts_and_network_rule(self) -> None:
        g = grade_evidence(ReasonFamily.FRAUD, [*LEDGER, ev("policy:n1")], min_score=0.12)
        assert g.sufficient and g.missing == ()

    def test_low_score_network_rule_does_not_count(self) -> None:
        g = grade_evidence(ReasonFamily.FRAUD, [*LEDGER, ev("policy:n1", score=0.05)], min_score=0.12)
        assert not g.sufficient and g.missing == ("network_rule",)

    def test_nad_needs_merchant_policy(self) -> None:
        g = grade_evidence(ReasonFamily.NOT_AS_DESCRIBED, [ev("policy:n1")], min_score=0.12)
        assert g.missing == ("merchant_policy",)

    def test_missing_ledger_facts_are_listed(self) -> None:
        g = grade_evidence(ReasonFamily.FRAUD, [ev("policy:n1")], min_score=0.12)
        assert g.missing == ("ledger:avs_cvv", "ledger:three_ds")


def verify(
    resolution: str,
    amount: str | None,
    cites: list[str],
    *,
    code: str = "13.1",
    disputed: str = "100.00",
    txn: str = "100.00",
    refunded: str = "0",
    now: datetime = NOW,
    evidence: list | None = None,
) -> list[str]:
    return verify_draft(
        resolution=resolution,
        amount=USD(amount) if amount else None,
        citations=cites,
        reason=lookup("VISA", code),
        disputed=USD(disputed),
        transaction_amount=USD(txn),
        refunded=USD(refunded),
        respond_by=NOW + timedelta(days=5),
        now=now,
        evidence=evidence if evidence is not None else [*LEDGER, ev("policy:n1")],
    )


class TestVerify:
    def test_valid_accept_passes(self) -> None:
        assert verify("ACCEPT", "100.00", ["policy:n1"]) == []

    @pytest.mark.parametrize(
        ("kwargs", "rule"),
        [
            ({"resolution": "ACCEPT", "amount": "999.00", "cites": ["policy:n1"]}, "R1"),
            ({"resolution": "PARTIAL", "amount": "100.00", "cites": ["policy:n1"], "code": "13.3"}, "R1"),
            ({"resolution": "PARTIAL", "amount": "50.00", "cites": ["policy:n1"]}, "R2"),
            (
                {"resolution": "ACCEPT", "amount": "100.00", "cites": ["policy:n1"], "now": NOW + timedelta(days=6)},
                "R3",
            ),
            (
                {
                    "resolution": "ACCEPT",
                    "amount": "100.00",
                    "cites": ["policy:n1"],
                    "txn": "300.00",
                    "refunded": "250.00",
                },
                "R4",
            ),
            ({"resolution": "CONTEST", "amount": None, "cites": ["policy:n1"]}, "R5"),
            ({"resolution": "ACCEPT", "amount": "100.00", "cites": []}, "R6"),
            ({"resolution": "ACCEPT", "amount": "100.00", "cites": ["policy:nope"]}, "R6"),
        ],
    )
    def test_each_rule_fires(self, kwargs: dict, rule: str) -> None:
        res, amt, cites = kwargs.pop("resolution"), kwargs.pop("amount"), kwargs.pop("cites")
        assert any(v.startswith(rule) for v in verify(res, amt, cites, **kwargs))

    def test_r4_boundary_holds(self) -> None:
        assert (
            verify("ACCEPT", "39.00", ["policy:n1"], code="12.6", disputed="39.00", txn="59.00", refunded="20.00") == []
        )

    def test_contest_with_compelling_evidence_passes(self) -> None:
        assert verify("CONTEST", None, ["ledger:delivery", "policy:n1"]) == []

    def test_request_info_needs_no_citation(self) -> None:
        assert verify("REQUEST_INFO", None, []) == []


def test_partial_amount_rounds_half_even() -> None:
    assert partial_amount(USD("45.25"), 50) == USD("22.62")
    assert partial_amount(USD("899.00"), 50) == USD("449.50")


class TestApprovalRoute:
    def r(self, res: Resolution, amount: str | None, *, hours_left: float = 480, auto: str | None = None):
        return approval_policy.route(
            resolution=res,
            amount=USD(amount) if amount else None,
            threshold=USD("250.00"),
            auto_approve_below=USD(auto) if auto else None,
            respond_by=NOW + timedelta(hours=hours_left),
            now=NOW,
            urgent_window=timedelta(hours=48),
            ttl=timedelta(hours=72),
            deadline_margin=timedelta(hours=24),
        )

    def test_threshold_is_inclusive(self) -> None:
        assert self.r(Resolution.ACCEPT, "250.00").required_role == "ops_lead"
        assert self.r(Resolution.ACCEPT, "249.99").required_role == "analyst"

    def test_contest_needs_analyst_whatever_the_amount(self) -> None:
        assert self.r(Resolution.CONTEST, None).required_role == "analyst"

    def test_urgent_escalates_and_caps_expiry(self) -> None:
        r = self.r(Resolution.ACCEPT, "75.00", hours_left=30)
        assert (r.required_role, r.priority, r.urgent) == ("ops_lead", "urgent", True)
        assert r.expires_at == NOW + timedelta(hours=6)  # respond_by - 24h beats now + 72h

    def test_close_to_the_deadline_the_approver_still_gets_time(self) -> None:
        assert self.r(Resolution.ACCEPT, "75.00", hours_left=24).expires_at == NOW + timedelta(hours=23, minutes=45)
        assert self.r(Resolution.ACCEPT, "75.00", hours_left=0.5).expires_at == NOW + timedelta(minutes=15)
        assert self.r(Resolution.ACCEPT, "75.00", hours_left=0.1).expires_at == NOW + timedelta(minutes=6)

    def test_auto_approve_only_below_limit_and_never_urgent_or_contest(self) -> None:
        assert self.r(Resolution.ACCEPT, "20.00", auto="50.00").auto
        assert not self.r(Resolution.ACCEPT, "60.00", auto="50.00").auto
        assert not self.r(Resolution.ACCEPT, "20.00", auto="50.00", hours_left=10).auto
        assert not self.r(Resolution.CONTEST, None, auto="50.00").auto

    def test_request_info_has_no_route(self) -> None:
        with pytest.raises(ValueError):
            self.r(Resolution.REQUEST_INFO, None)

    def test_idempotency_key_is_stable_and_amount_sensitive(self) -> None:
        def k(a: str) -> str:
            return approval_policy.idempotency_key(
                tenant_id="t", dispute_id="d", thread_id="th", kind="refund", amount=USD(a)
            )

        assert k("10.00") == k("10.00") != k("10.01")


class TestPathTo:
    def test_paths(self) -> None:
        S = DisputeStatus
        assert path_to(S.EVIDENCE_GATHERING, S.RESOLVED_ACCEPTED) == ["submit_for_review", "resolve_accept"]
        assert path_to(S.UNDER_REVIEW, S.RESOLVED_CONTESTED) == ["resolve_contest"]
        assert path_to(S.UNDER_REVIEW, S.EVIDENCE_GATHERING) == ["start_evidence"]
        assert path_to(S.RESOLVED_ACCEPTED, S.RESOLVED_ACCEPTED) == []  # replay is a no-op
        with pytest.raises(InvalidTransition):
            path_to(S.WITHDRAWN, S.RESOLVED_ACCEPTED)
