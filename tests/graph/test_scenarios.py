"""T15.3 traceability: every Gherkin scenario in specs/ is proven by a named test that exists.

Adding a scenario without a test, or renaming a test a scenario points to, fails here."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

G = "tests.graph.test_resolution_graph"
A = "tests.api.test_approvals_api"
I = "tests.integration"  # noqa: E741

SCENARIOS: dict[str, list[str]] = {
    # specs/resolution.feature
    "Fraud dispute with 3-D Secure and matching AVS/CVV pauses for analyst approval": [
        f"{G}::test_o11_fraud_with_3ds_pauses_for_analyst_approval"
    ],
    "Not-as-described dispute with no merchant evidence asks for information": [
        f"{G}::test_o4_request_info_moves_dispute_back_to_evidence_gathering"
    ],
    "Retrieval finds nothing useful twice and the workflow abstains": [
        f"{G}::test_o5_retriever_that_finds_nothing_abstains_after_two_rounds"
    ],
    "Draft that exceeds the disputed amount is refined once, then abstains": [
        f"{G}::test_o6_overpaying_drafter_is_refined_once_then_abstains"
    ],
    "Dispute past its response deadline is expired, not resolved": [
        f"{G}::test_o9_deadline_passed_expires_the_dispute"
    ],
    "A second resolution while one awaits approval is refused": [
        f"{G}::test_a1_second_start_while_awaiting_approval_is_409"
    ],
    "A dispute that is already resolved cannot start a resolution": [f"{G}::test_a2_dispute_not_resolvable_is_409"],
    # specs/approval.feature
    "Refund at or above the threshold needs an ops lead": [f"{A}::test_role_four_eyes_and_stale_payload_are_refused"],
    "The requester cannot approve their own resolution": [f"{A}::test_role_four_eyes_and_stale_payload_are_refused"],
    "Fewer than 48 hours to the deadline routes to an ops lead as urgent": [
        f"{G}::test_urgent_dispute_routes_to_ops_lead",
        "tests.unit.test_rules::TestApprovalRoute::test_urgent_escalates_and_caps_expiry",
    ],
    "Rejection ends the run without side effects": [f"{A}::test_rejection_and_queue_visibility"],
    "Two decisions on the same approval — only the first wins": [
        f"{A}::test_two_simultaneous_decisions_one_wins_one_side_effect"
    ],
    "An approval not decided before its TTL expires": [f"{A}::test_expiry_by_sweeper_then_late_decision_is_410"],
    "The approval queue shows only what my role can decide": [f"{A}::test_rejection_and_queue_visibility"],
    # specs/execution.feature
    "Approved full refund": [f"{A}::test_approved_refund_runs_end_to_end_with_the_payments_server"],
    "Approved partial refund": [f"{I}.test_api_v2::test_approved_partial_refund"],
    "Approved representment": [f"{G}::test_o3_approved_contest_submits_representment"],
    "Transient gateway errors are retried": [
        f"{I}.test_tool_gateway::test_transient_failures_retry_with_the_same_key_then_succeed"
    ],
    "Permanent gateway error ends in execution failure": [f"{G}::test_o8_expiry_and_o10_permanent_failure"],
    "Process crash after the refund but before the dispute update": [
        f"{G}::test_crash_between_refund_and_dispute_update_recovers_without_repeating_the_refund",
        "tests.chaos.test_crash_resume::test_cancelled_mid_execute_after_the_refund_recovers_once",
    ],
    "Refund would exceed the transaction after prior refunds": [
        "tests.unit.test_rules::TestVerify::test_each_rule_fires",
        "evals:G22",
    ],
    # specs/isolation_and_compat.feature
    "Another tenant's resources are invisible": [f"{I}.test_isolation::test_other_tenant_gets_404_everywhere"],
    "Retrieval never returns another tenant's merchant policy": [
        f"{I}.test_isolation::test_retrieval_never_returns_another_tenants_merchant_policy"
    ],
    "A v1 assist thread continues after the upgrade": [
        "tests.graph.test_checkpoint_compat::test_assist_fixture_resumes_with_history_and_upgraded_schema"
    ],
    "A run that exceeds the API timeout returns 504 and stays resumable": [
        f"{I}.test_api_v2::test_run_over_the_api_timeout_returns_504_and_stays_resumable"
    ],
}


def feature_scenarios() -> list[str]:
    titles = []
    for f in sorted(Path("specs").glob("*.feature")):
        titles += [m.group(2).strip() for m in re.finditer(r"^\s*Scenario( Outline)?:\s*(.+)$", f.read_text(), re.M)]
    return titles


def test_every_scenario_is_mapped() -> None:
    titles = feature_scenarios()
    assert len(titles) == len(set(titles))
    assert set(titles) == set(SCENARIOS), {
        "unmapped": set(titles) - set(SCENARIOS),
        "stale": set(SCENARIOS) - set(titles),
    }


@pytest.mark.parametrize("ref", sorted({r for refs in SCENARIOS.values() for r in refs}))
def test_every_referenced_proof_exists(ref: str) -> None:
    if ref.startswith("evals:"):
        import json

        ids = {
            json.loads(line)["id"] for p in Path("evals").glob("golden_v*.jsonl") for line in p.read_text().splitlines()
        }
        assert ref.split(":")[1] in ids
        return
    module, _, attr = ref.partition("::")
    obj: object = importlib.import_module(module)
    for part in attr.split("::"):
        obj = getattr(obj, part)
    assert callable(obj)
