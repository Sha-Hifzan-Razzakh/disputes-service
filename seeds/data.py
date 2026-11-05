"""Seed records: two merchant tenants, ledger transactions, network-rule summaries, merchant policies.

Network-rule texts are internal summaries written for this service; they are illustrative, not
quotations of any card-network rulebook.
"""

from __future__ import annotations

from decimal import Decimal

TENANTS = [
    {"id": "northwind", "name": "Northwind Outfitters", "currency": "USD"},
    {"id": "aurora", "name": "Aurora Electronics", "currency": "EUR"},
]

# (tenant, ref, amount, card, avs, cvv, 3ds, delivery_status, tracking, refunded, description, days_ago)
TRANSACTIONS = [
    (
        "northwind",
        "NW-1001",
        "189.00",
        "VISA",
        "Y",
        "M",
        True,
        "DELIVERED",
        "1Z999AA10123456784",
        "0",
        "Trail backpack 45L",
        20,
    ),
    (
        "northwind",
        "NW-1002",
        "640.00",
        "VISA",
        "N",
        "N",
        False,
        "DELIVERED",
        "1Z999AA10123456785",
        "0",
        "Two-person tent, expedition",
        18,
    ),
    (
        "northwind",
        "NW-1003",
        "89.50",
        "VISA",
        "Y",
        "M",
        False,
        "IN_TRANSIT",
        "1Z999AA10123456786",
        "0",
        "Merino base layer set",
        12,
    ),
    (
        "northwind",
        "NW-1004",
        "1249.00",
        "MASTERCARD",
        "Y",
        "M",
        True,
        "DELIVERED",
        "1Z999AA10123456787",
        "0",
        "Carbon trekking poles + GPS watch bundle",
        25,
    ),
    (
        "northwind",
        "NW-1005",
        "75.00",
        "VISA",
        "Y",
        "M",
        False,
        "DELIVERED",
        "1Z999AA10123456788",
        "0",
        "Headlamp (charged twice)",
        9,
    ),
    ("northwind", "NW-1006", "310.00", "MASTERCARD", "N", "M", False, None, None, "0", "Down jacket", 15),
    (
        "northwind",
        "NW-1007",
        "540.00",
        "VISA",
        "Y",
        "M",
        True,
        "DELIVERED",
        "1Z999AA10123456790",
        "0",
        "Insulated sleeping system",
        22,
    ),
    (
        "aurora",
        "AU-2001",
        "899.00",
        "VISA",
        "Y",
        "M",
        True,
        "DELIVERED",
        "JD014600006281234567",
        "0",
        "Noise-cancelling headphones",
        16,
    ),
    (
        "aurora",
        "AU-2002",
        "1899.00",
        "MASTERCARD",
        "N",
        "N",
        False,
        "DELIVERED",
        "JD014600006281234568",
        "0",
        "Laptop 14in",
        30,
    ),
    (
        "aurora",
        "AU-2003",
        "129.00",
        "VISA",
        "Y",
        "M",
        False,
        "RETURNED",
        "JD014600006281234569",
        "0",
        "Smart plug 4-pack",
        11,
    ),
    (
        "aurora",
        "AU-2004",
        "349.00",
        "MASTERCARD",
        "Y",
        "M",
        False,
        "DELIVERED",
        "JD014600006281234570",
        "0",
        "Mechanical keyboard (opened)",
        8,
    ),
    (
        "aurora",
        "AU-2005",
        "59.00",
        "VISA",
        "Y",
        "M",
        False,
        "DELIVERED",
        "JD014600006281234571",
        "59.00",
        "USB-C dock (already refunded)",
        19,
    ),
]

NETWORK_DOCS = [
    (
        "Network rules summary: card-absent fraud (Visa 10.4, Mastercard 4837)",
        "internal-network-summary",
        """
For card-absent fraud disputes the cardholder states they did not authorise the purchase. The merchant can contest when the transaction was authenticated with 3-D Secure, because successful authentication shifts fraud liability to the issuer.

Compelling evidence for card-absent fraud includes a full AVS match together with a CVV match, prior undisputed transactions on the same card from the same device or shipping address, and proof that the goods were delivered to the cardholder's billing address.

Without 3-D Secure authentication and without matching AVS and CVV results the merchant should normally accept the dispute and refund the cardholder, because a representment is unlikely to succeed.
""",
    ),
    (
        "Network rules summary: merchandise not received (Visa 13.1)",
        "internal-network-summary",
        """
For merchandise-not-received disputes the cardholder states the goods never arrived. The merchant can contest only with proof of delivery: a carrier tracking record that shows the parcel delivered to the address the cardholder provided, with delivery date.

If the tracking record shows the parcel still in transit, returned, or lost, the merchant should accept the dispute and refund, or ship a replacement only if the cardholder agrees in writing.

For high-value orders a signature on delivery strengthens the representment considerably.
""",
    ),
    (
        "Network rules summary: not as described or defective (Visa 13.3, Mastercard 4853)",
        "internal-network-summary",
        """
For not-as-described disputes the cardholder states the goods differ from the description or are defective. The merchant should provide the product description shown at the time of sale and any customer communications.

When the item was used, opened or partly consumed, a partial refund that deducts a disclosed restocking or usage fee may be appropriate; the remainder can be contested with the disclosed return policy as evidence.

If the cardholder returned the item and the merchant received it, the merchant should refund in full.
""",
    ),
    (
        "Network rules summary: processing errors (Visa 12.6, Mastercard 4834)",
        "internal-network-summary",
        """
For duplicate processing or point-of-interaction errors the cardholder states they were charged twice or charged incorrectly. The merchant should check the ledger for a second capture of the same amount.

If the charge was duplicated the merchant should accept and refund the duplicate amount. If a refund was already issued, the merchant can contest by providing the refund record, because the cardholder has already been credited.
""",
    ),
    (
        "Network rules summary: response deadlines",
        "internal-network-summary",
        """
Merchants must respond to a dispute before the response deadline. In this service the default response window is 30 days for Visa and 45 days for Mastercard from the date the dispute was opened.

A dispute that is not answered before the deadline is lost by default and the cardholder keeps the credit. Disputes within 48 hours of their deadline should be escalated to an operations lead.
""",
    ),
]

TENANT_DOCS = {
    "northwind": [
        (
            "Northwind refund and dispute policy",
            "northwind-handbook-v3",
            """
Northwind Outfitters accepts returns of unused outdoor gear within 30 days of delivery with a full refund to the original card.

Refunds or dispute acceptances of 500 USD or more require sign-off from an operations lead before money is returned. Analysts may approve refunds below 500 USD.

Orders above 250 USD are shipped with signature on delivery. For fraud disputes on 3-D Secure authenticated orders, Northwind contests by default.
""",
        )
    ],
    "aurora": [
        (
            "Aurora Electronics returns and chargeback policy",
            "aurora-policy-2026",
            """
Aurora Electronics accepts returns within 14 days of delivery. Opened electronics incur a 10 percent restocking fee, which is disclosed at checkout.

For not-as-described disputes on opened items Aurora offers a partial refund of the purchase price minus the restocking fee and contests the remainder.

Any refund or dispute acceptance of 750 EUR or more requires approval by an operations lead. Contested disputes are always reviewed by an analyst before submission to the card network.
""",
        )
    ],
}

# Tenant dispute policy (matches the merchant policy documents above): approval threshold in the tenant currency,
# auto-approve limit (None = off), partial-refund percentage.
TENANT_POLICIES = {
    "northwind": ("500.00", None, 50),
    "aurora": ("750.00", None, 50),
}

# (tenant, txn_ref, network, reason_code, amount, status)
DISPUTES = [
    ("northwind", "NW-1001", "VISA", "10.4", "189.00", "OPENED"),
    ("northwind", "NW-1002", "VISA", "10.4", "640.00", "EVIDENCE_GATHERING"),
    ("northwind", "NW-1003", "VISA", "13.1", "89.50", "OPENED"),
    ("northwind", "NW-1005", "VISA", "12.6", "75.00", "UNDER_REVIEW"),
    ("aurora", "AU-2004", "MASTERCARD", "4853", "349.00", "EVIDENCE_GATHERING"),
    ("aurora", "AU-2002", "MASTERCARD", "4837", "1899.00", "OPENED"),
]


def money(s: str) -> Decimal:
    return Decimal(s)
