"""Turn the case snapshot into evidence items (ledger facts and merchant uploads).

Ledger facts always appear (so grading can see they were checked), but their `kind` only names compelling
evidence when the fact favours the merchant: e.g. `three_ds_result` when 3-D Secure authenticated, otherwise
`three_ds_absent`. Scores are 1.0: these are records, not search hits.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

EXCERPT_CHARS = 400


def _item(id_: str, source: str, kind: str, title: str, excerpt: str) -> dict[str, Any]:
    return {
        "id": id_,
        "source": source,
        "scope": None,
        "kind": kind,
        "title": title,
        "excerpt": excerpt[:EXCERPT_CHARS],
        "score": 1.0,
    }


def ledger_evidence(txn: Mapping[str, Any]) -> list[dict[str, Any]]:
    tds = bool(txn.get("three_ds_authenticated"))
    avs, cvv = txn.get("avs_result"), txn.get("cvv_result")
    status, tracking = txn.get("delivery_status"), txn.get("delivery_tracking")
    refunded = Decimal(str(txn.get("refunded", "0")))
    delivered = status == "DELIVERED" and bool(tracking)
    return [
        _item(
            "ledger:three_ds",
            "ledger",
            "three_ds_result" if tds else "three_ds_absent",
            "3-D Secure",
            f"3-D Secure {'authenticated' if tds else 'not authenticated'} for {txn.get('ref')}",
        ),
        _item(
            "ledger:avs_cvv",
            "ledger",
            "avs_cvv_result" if (avs == "Y" and cvv == "M") else "avs_cvv_mismatch",
            "AVS / CVV",
            f"AVS result {avs or 'none'}, CVV result {cvv or 'none'}",
        ),
        _item(
            "ledger:delivery",
            "ledger",
            "delivery_proof" if delivered else "delivery_status",
            "Delivery",
            f"Carrier status {status or 'none'}, tracking {tracking or 'none'}",
        ),
        _item(
            "ledger:refunds",
            "ledger",
            "refund_record" if refunded > 0 else "refund_none",
            "Refunds",
            f"Prior refunds {refunded} on a {txn.get('amount')} transaction",
        ),
    ]


def merchant_evidence(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        _item(f"merchant:{e['id']}", "merchant", str(e["kind"]), f"Merchant upload: {e['kind']}", str(e["description"]))
        for e in items
    ]
