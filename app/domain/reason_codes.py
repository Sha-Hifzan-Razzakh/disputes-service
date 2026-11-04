"""Card-network reason-code catalogue.

Titles and response windows are illustrative values for this service, not a copy of
network rulebooks; tenants can override response windows in configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.errors import NotFound


class Network(StrEnum):
    VISA = "VISA"
    MASTERCARD = "MASTERCARD"


class ReasonFamily(StrEnum):
    FRAUD = "FRAUD"
    NOT_RECEIVED = "NOT_RECEIVED"
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    PROCESSING_ERROR = "PROCESSING_ERROR"


class Resolution(StrEnum):
    ACCEPT = "ACCEPT"  # refund the cardholder in full
    PARTIAL = "PARTIAL"  # refund part, contest the rest
    CONTEST = "CONTEST"  # representment with compelling evidence
    REQUEST_INFO = "REQUEST_INFO"  # ask the merchant for more evidence


@dataclass(frozen=True, slots=True)
class ReasonCode:
    network: Network
    code: str
    title: str
    family: ReasonFamily
    allowed: tuple[Resolution, ...]
    required_evidence: tuple[str, ...]


_FAMILY_RULES: dict[ReasonFamily, tuple[tuple[Resolution, ...], tuple[str, ...]]] = {
    ReasonFamily.FRAUD: (
        (Resolution.ACCEPT, Resolution.CONTEST, Resolution.REQUEST_INFO),
        ("three_ds_result", "avs_cvv_result"),
    ),
    ReasonFamily.NOT_RECEIVED: ((Resolution.ACCEPT, Resolution.CONTEST, Resolution.REQUEST_INFO), ("delivery_proof",)),
    ReasonFamily.NOT_AS_DESCRIBED: (
        (Resolution.ACCEPT, Resolution.PARTIAL, Resolution.CONTEST, Resolution.REQUEST_INFO),
        ("product_description", "customer_comms"),
    ),
    ReasonFamily.PROCESSING_ERROR: (
        (Resolution.ACCEPT, Resolution.CONTEST, Resolution.REQUEST_INFO),
        ("transaction_records",),
    ),
}

_CODES: list[tuple[Network, str, str, ReasonFamily]] = [
    (Network.VISA, "10.4", "Other fraud - card-absent environment", ReasonFamily.FRAUD),
    (Network.VISA, "13.1", "Merchandise or services not received", ReasonFamily.NOT_RECEIVED),
    (Network.VISA, "13.3", "Not as described or defective", ReasonFamily.NOT_AS_DESCRIBED),
    (Network.VISA, "12.6", "Duplicate processing or paid by other means", ReasonFamily.PROCESSING_ERROR),
    (Network.MASTERCARD, "4837", "No cardholder authorization", ReasonFamily.FRAUD),
    (Network.MASTERCARD, "4853", "Cardholder dispute", ReasonFamily.NOT_AS_DESCRIBED),
    (Network.MASTERCARD, "4834", "Point-of-interaction error", ReasonFamily.PROCESSING_ERROR),
]

CATALOG: dict[tuple[Network, str], ReasonCode] = {
    (net, code): ReasonCode(net, code, title, fam, *_FAMILY_RULES[fam]) for net, code, title, fam in _CODES
}

# Days the merchant has to respond after a dispute is opened (illustrative defaults).
RESPONSE_WINDOW_DAYS: dict[Network, int] = {Network.VISA: 30, Network.MASTERCARD: 45}


def lookup(network: Network | str, code: str) -> ReasonCode:
    try:
        return CATALOG[(Network(network), code)]
    except (KeyError, ValueError):
        raise NotFound("reason code", f"{network}:{code}") from None
