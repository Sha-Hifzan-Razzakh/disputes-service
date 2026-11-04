from datetime import UTC, datetime, timedelta

import pytest

from app.domain.dispute import Dispute, DisputeStatus
from app.domain.errors import DomainError, InvalidTransition, NotFound
from app.domain.money import Money
from app.domain.reason_codes import Network, ReasonFamily, Resolution, lookup

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _open(amount: str = "100.00", txn: str = "100.00") -> Dispute:
    return Dispute.open(
        tenant_id="t1",
        reason=lookup("VISA", "13.1"),
        transaction_ref="R1",
        amount=Money.of(amount, "USD"),
        transaction_amount=Money.of(txn, "USD"),
        actor="u",
        now=NOW,
    )


class TestMoney:
    def test_rounds_half_even_to_cents(self) -> None:
        assert Money.of("10.005", "USD").amount == Money.of("10.00", "USD").amount

    def test_rejects_negative_and_unknown_currency(self) -> None:
        with pytest.raises(DomainError):
            Money.of("-1", "USD")
        with pytest.raises(DomainError):
            Money.of("1", "XYZ")

    def test_currency_mismatch(self) -> None:
        with pytest.raises(DomainError):
            _ = Money.of("1", "USD") + Money.of("1", "EUR")


class TestReasonCodes:
    def test_lookup_and_family(self) -> None:
        rc = lookup("MASTERCARD", "4853")
        assert rc.network is Network.MASTERCARD and rc.family is ReasonFamily.NOT_AS_DESCRIBED
        assert Resolution.PARTIAL in rc.allowed

    def test_unknown_code(self) -> None:
        with pytest.raises(NotFound):
            lookup("VISA", "99.9")


class TestLifecycle:
    def test_open_sets_deadline_by_network(self) -> None:
        d = _open()
        assert d.status is DisputeStatus.OPENED
        assert d.respond_by == NOW + timedelta(days=30)
        assert d.pending_events[0].kind == "opened"

    def test_amount_cannot_exceed_transaction(self) -> None:
        with pytest.raises(DomainError):
            _open(amount="150.00", txn="100.00")

    def test_happy_path_increments_version(self) -> None:
        d = _open()
        for a in ("start_evidence", "submit_for_review", "resolve_contest"):
            d.apply(a, actor="u", now=NOW)
        assert d.status is DisputeStatus.RESOLVED_CONTESTED and d.version == 4 and d.is_terminal

    def test_invalid_transition(self) -> None:
        d = _open()
        with pytest.raises(InvalidTransition):
            d.apply("resolve_accept", actor="u", now=NOW)

    def test_cannot_resolve_after_deadline(self) -> None:
        d = _open()
        d.apply("start_evidence", actor="u", now=NOW)
        d.apply("submit_for_review", actor="u", now=NOW)
        with pytest.raises(DomainError):
            d.apply("resolve_accept", actor="u", now=NOW + timedelta(days=31))

    def test_cannot_assign_terminal(self) -> None:
        d = _open()
        d.apply("withdraw", actor="u", now=NOW)
        with pytest.raises(InvalidTransition):
            d.assign("x", actor="u", now=NOW)
