"""Independent encoding of the spec's decision rules (docs/spec.md §4) used to label generated golden cases.

Deliberately imports nothing from `app/`: if the service and this oracle disagree, one of them misreads the spec.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

FAMILY = {
    ("VISA", "10.4"): "FRAUD",
    ("MASTERCARD", "4837"): "FRAUD",
    ("VISA", "13.1"): "NOT_RECEIVED",
    ("VISA", "13.3"): "NOT_AS_DESCRIBED",
    ("MASTERCARD", "4853"): "NOT_AS_DESCRIBED",
    ("VISA", "12.6"): "PROCESSING_ERROR",
    ("MASTERCARD", "4834"): "PROCESSING_ERROR",
}
START = ["load_case", "classify_reason", "gather_evidence"]
DRAFT = ["draft_resolution", "verify"]


def _d(x: Any) -> Decimal:
    return Decimal(str(x))


def resolution(case: dict[str, Any]) -> tuple[str, Decimal | None]:
    fam = FAMILY[(case["network"], case["reason_code"])]
    t, up = case["transaction"], set(case["merchant_evidence"])
    disputed = _d(case["disputed_amount"])
    if fam == "FRAUD":
        if t["three_ds_authenticated"]:
            return "CONTEST", None
        if t["avs_result"] == "Y" and t["cvv_result"] == "M":
            return ("CONTEST", None) if up & {"customer_comms", "transaction_records"} else ("REQUEST_INFO", None)
        return "ACCEPT", disputed
    if fam == "NOT_RECEIVED":
        if t["delivery_status"] == "DELIVERED":
            return ("CONTEST", None) if t["delivery_tracking"] else ("REQUEST_INFO", None)
        return "ACCEPT", disputed
    if fam == "NOT_AS_DESCRIBED":
        if {"product_description", "customer_comms"} <= up:
            return "CONTEST", None
        if "product_description" in up:
            return "PARTIAL", (disputed * Decimal(case.get("partial_pct", 50)) / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
        return "REQUEST_INFO", None
    return ("CONTEST", None) if _d(t["refunded"]) >= disputed else ("ACCEPT", disputed)


def expected(case: dict[str, Any]) -> dict[str, Any]:
    fixture = case.get("fixture")
    if case["days_to_deadline"] <= 0:
        return {"outcome": "DEADLINE_PASSED", "resolution": None, "path": ["load_case", "close_expired", "notify"]}
    if fixture in ("empty_policy_store", "network_docs_only") and not (
        fixture == "network_docs_only" and FAMILY[(case["network"], case["reason_code"])] != "NOT_AS_DESCRIBED"
    ):
        return {
            "outcome": "ABSTAINED_INSUFFICIENT_EVIDENCE",
            "resolution": None,
            "retrieval_attempts": 2,
            "path": [*START, "escalate_insufficient", "notify"],
        }
    if fixture == "drafter_overpays":
        return {
            "outcome": "ABSTAINED_FAILED_VERIFICATION",
            "resolution": None,
            "violations": ["R1"],
            "path": [*START, *DRAFT, *DRAFT, "escalate_unverified", "notify"],
        }
    res, amount = resolution(case)
    refine = [*DRAFT] if fixture == "drafter_forgets_citations_once" else []
    t = case["transaction"]
    if amount is not None and _d(t["refunded"]) + amount > _d(t["amount"]):
        return {
            "outcome": "ABSTAINED_FAILED_VERIFICATION",
            "resolution": None,
            "violations": ["R4"],
            "path": [*START, *DRAFT, *DRAFT, "escalate_unverified", "notify"],
        }
    if res == "REQUEST_INFO":
        return {
            "outcome": "INFO_REQUESTED",
            "resolution": "REQUEST_INFO",
            "path": [*START, *DRAFT, *refine, "prepare_action", "request_info", "notify"],
        }
    urgent = case["days_to_deadline"] * 24 < 48
    role = "ops_lead" if (amount is not None and amount >= _d(case["approval_threshold"])) or urgent else "analyst"
    return {
        "outcome": "AWAITING_APPROVAL",
        "resolution": res,
        "amount": str(amount) if amount is not None else None,
        "required_role": role,
        "priority": "urgent" if urgent else "normal",
        "path": [*START, *DRAFT, *refine, "prepare_action", "request_approval"],
        "after_approval": {
            "outcome": {"ACCEPT": "REFUNDED", "PARTIAL": "PARTIALLY_REFUNDED", "CONTEST": "REPRESENTED"}[res],
            "tool": "network.submit_representment" if res == "CONTEST" else "payments.issue_refund",
            "path": ["approve", "execute", "apply_resolution", "notify"],
        },
    }
