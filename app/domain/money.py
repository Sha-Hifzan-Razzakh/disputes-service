from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from app.domain.errors import DomainError

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "AED"}
_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Money:
    """Amount in a single currency, always two decimal places, never negative."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise DomainError(f"unsupported currency {self.currency}")
        amt = Decimal(self.amount).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        if amt < 0:
            raise DomainError("money cannot be negative")
        object.__setattr__(self, "amount", amt)

    @classmethod
    def of(cls, amount: str | int | float | Decimal, currency: str) -> Money:
        return cls(Decimal(str(amount)), currency)

    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise DomainError(f"currency mismatch {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount <= other.amount

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount < other.amount

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
