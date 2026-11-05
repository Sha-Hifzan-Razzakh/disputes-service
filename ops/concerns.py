"""Single source for the concern -> mechanism -> tool table (T27.1-T27.3). Generates docs/concerns.md:

    uv run python ops/concerns.py            # write
    uv run python ops/concerns.py --check    # CI: fail if docs/concerns.md is stale

One row per "what happens if ...?" question. Every row names the mechanism that answers it, where it lives, and
the proofs (tests, evals, CI jobs, rehearsals). Every incident gets a row (or extends one) and a control
(.claude/skills/incident-to-concern). tests/unit/test_concerns.py checks every proof still exists.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "concerns.md"


@dataclass(frozen=True)
class Concern:
    id: str
    question: str  # what happens if ...
    mechanism: str
    where: str
    proofs: tuple[str, ...]  # test refs "module::func", "evals:<id>", "ci:<workflow>/<job>", "drill:<script>"
    incidents: tuple[str, ...] = field(default_factory=tuple)
    status: str = "controlled"  # controlled | accepted (risk accepted, reason in mechanism)


G = "tests.graph.test_resolution_graph"
M = "tests.graph.test_money_safety"
A = "tests.api.test_approvals_api"
L = "tests.api.test_lost_resume"
T = "tests.integration.test_tool_gateway"
U = "tests.unit"

CONCERNS: list[Concern] = [
    # --- money moves exactly once, only as approved -------------------------------------------------------
    Concern(
        "C01",
        "the process crashes after the refund but before the dispute is updated?",
        "idempotency key fixed before the pause; side-effect ledger reserve/finish; provider dedupes; recover",
        "domain/approval_policy.py, SqlSideEffectLedger, execute",
        (
            f"{G}::test_crash_between_refund_and_dispute_update_recovers_without_repeating_the_refund",
            "tests.chaos.test_crash_resume::test_cancelled_mid_execute_after_the_refund_recovers_once",
        ),
    ),
    Concern(
        "C02",
        "a refund times out but actually succeeded, and someone starts a new resolution?",
        "unknown outcome is reconciled by key; new starts refused while a side effect is unsettled",
        "ResolutionRunner.start, sweeper reconcile",
        (f"{M}::test_r1_unsettled_side_effect_blocks_a_second_resolution",),
        incidents=("PR #1 review R1",),
    ),
    Concern(
        "C03",
        "the case changes (resolved by hand, withdrawn, refunded) while the approval waits?",
        "execute re-reads the case and sends nothing if it changed",
        "resolution.execute, _case_changed",
        (f"{M}::test_r2_case_changed_after_approval_sends_nothing",),
        incidents=("PR #1 review R2",),
    ),
    Concern(
        "C04",
        "a provider answers a successful refund with something unreadable?",
        "non-JSON success is malformed -> unknown -> reconciled; never a clean failure",
        "tools/mcp_client.py, gateway._attempt",
        (
            "tests.unit.test_adapters::test_r3_unreadable_success_from_a_side_effect_is_unknown_not_failed",
            "tests.chaos.test_tool_malformed::test_malformed_answer_after_refund_is_unknown_then_reconciled",
        ),
        incidents=("PR #1 review R3",),
    ),
    Concern(
        "C05",
        "an approval is reused for another dispute, amount, tool or key?",
        "approval summary binds dispute, tool, idempotency key, amount, evidence; providers refuse key reuse",
        "gateway._bound, mcp_servers",
        (
            f"{T}::test_r4_an_approval_authorises_exactly_one_call",
            f"{T}::test_r4_provider_refuses_a_reused_key_with_other_arguments",
        ),
        incidents=("PR #1 review R4",),
    ),
    Concern(
        "C06",
        "a draft refunds the wrong amount (over the dispute, under it for ACCEPT, past prior refunds)?",
        "verify rules R1-R6 before approval; refine once, then abstain",
        "domain/rules.py::verify_draft",
        (
            f"{U}.test_rules::TestVerify::test_each_rule_fires",
            f"{G}::test_o6_overpaying_drafter_is_refined_once_then_abstains",
            "evals:G22",
        ),
        incidents=("PR #1 review R8",),
    ),
    Concern(
        "C07",
        "two approvers decide at the same time, or the requester approves their own draft?",
        "compare-and-set decision; four-eyes; role by amount and urgency",
        "SqlApprovalRepository.decide",
        (
            f"{A}::test_two_simultaneous_decisions_one_wins_one_side_effect",
            f"{A}::test_role_four_eyes_and_stale_payload_are_refused",
        ),
    ),
    Concern(
        "C08",
        "a refund is in another currency than the tenant's approval threshold?",
        "routed to an ops lead, never auto-approved (no FX comparison)",
        "domain/approval_policy.py::route",
        (f"{U}.test_rules::TestApprovalRoute::test_r9_refund_in_another_currency_goes_to_ops_lead_and_never_auto",),
        incidents=("PR #1 review R9",),
    ),
    # --- concurrency and recovery -------------------------------------------------------------------------
    Concern(
        "C09",
        "two workers run the same thread at once?",
        "per-thread advisory lock on an autocommit connection; resume waits 5 s",
        "SqlThreadRegistry.exclusive",
        (
            "tests.chaos.test_concurrent_resume::test_one_resume_wins_the_thread_lock",
            f"{L}::test_r16_thread_lock_is_not_held_inside_an_open_transaction",
        ),
        incidents=("PR #1 review R16",),
    ),
    Concern(
        "C10",
        "a decision is stored but its resume cannot run (busy thread, process full)?",
        "thread marked resume_failed; admin recover replays the stored decision",
        "routers/approvals.py, admin.py",
        (
            f"{L}::test_r5_decision_that_cannot_resume_becomes_recoverable",
            f"{L}::test_r5_stranded_awaiting_thread_with_a_decided_approval_can_be_recovered",
        ),
        incidents=("PR #1 review R5",),
    ),
    Concern(
        "C11",
        "the checkpointer write after a side effect is lost?",
        "durability=sync; replay finds the side effect and makes no second call",
        "runners._invoke",
        ("tests.chaos.test_checkpointer_failure::test_lost_checkpoint_after_execute_does_not_repeat_the_refund",),
    ),
    Concern(
        "C12",
        "the database drops a connection or slows down mid-run?",
        "TransientError -> node RetryPolicy; idempotent writes",
        "workflow_store, RetryPolicy",
        (
            "tests.chaos.test_db_faults::test_connection_reset_during_approval_request_creates_one_row",
            "tests.chaos.test_db_faults::test_db_latency_is_tolerated",
        ),
    ),
    Concern(
        "C13",
        "the model times out, rate-limits or stays down?",
        "transient errors retried by the node; a model that stays down fails cleanly and recovers",
        "drafting.LlmResolutionDrafter, RetryPolicy",
        ("tests.chaos.test_model_failures::test_model_down_fails_cleanly_and_recovers",),
    ),
    Concern(
        "C14",
        "more runs arrive than the process can hold?",
        "admission control: wait 2 s, then 429 with Retry-After; settings refuse more slots than connections",
        "api/deps.py::bounded_run, settings",
        ("tests.integration.test_api_v2::test_admission_control_refuses_with_429_when_full",),
    ),
    # --- tools and providers ------------------------------------------------------------------------------
    Concern(
        "C15",
        "a provider changes a tool's schema while we are connected?",
        "pins re-checked on every (re)connect; nothing sent on an unverified session",
        "gateway.verify",
        (f"{T}::test_r10_schema_pins_are_checked_again_after_every_reconnect",),
        incidents=("PR #1 review R10",),
    ),
    Concern(
        "C16",
        "an MCP server is reached by its service name inside the cluster?",
        "DNS-rebinding protection allows localhost and the service name only",
        "mcp_servers/common.py::transport_security",
        (
            "tests.integration.test_mcp_servers::test_servers_answer_on_their_service_name_and_refuse_other_hosts",
            "ci:deploy/staging",
        ),
        incidents=("first staging deploy: every in-cluster MCP call got 421",),
    ),
    Concern(
        "C17",
        "a provider is down for a long time?",
        "kill switch APP_TOOLS_ENABLED=false; approvals keep working; alert ToolCallsMostlyFailing",
        "build_tool_gateway, runbook",
        (f"{G}::test_tools_kill_switch_ends_in_execution_failed",),
    ),
    # --- tenants and security -----------------------------------------------------------------------------
    Concern(
        "C18",
        "one tenant asks for another tenant's dispute, thread, approval or policy?",
        "tenant from the token; every lookup filtered by tenant; 404, never 403",
        "ThreadRegistry.get, SqlPolicyStore",
        (
            "tests.integration.test_isolation::test_other_tenant_gets_404_everywhere",
            "tests.integration.test_isolation::test_retrieval_never_returns_another_tenants_merchant_policy",
        ),
    ),
    Concern(
        "C19",
        "the analyst note tries to rewrite the rules (prompt injection)?",
        "note fenced, every fence tag stripped until none remain; verify + human approval",
        "drafting.resolution_prompt",
        ("tests.unit.test_structured::test_r14_note_can_never_close_or_reopen_its_fence", "evals:RT-01"),
        incidents=("PR #1 review R14",),
    ),
    Concern(
        "C20",
        "card numbers or secrets reach logs, spans or metrics?",
        "redaction processor; args hashed in tool_calls; no frame locals in tracebacks",
        "bootstrap/observability.py",
        ("tests.api.test_observability::test_r15_exception_logs_carry_type_message_and_frames_but_no_locals",),
    ),
    Concern(
        "C21",
        "the image ships a fixable HIGH vulnerability?",
        "Grype in CI fails the build; base packages upgraded in the image; Dependabot",
        "ci.yml image job, Dockerfile",
        ("ci:ci/image",),
        incidents=("apt CVE-2026-109518 found by the first scan",),
    ),
    Concern(
        "C22",
        "a pod is compromised and tries to reach the internet or the database directly?",
        "default-deny NetworkPolicies; prod security groups by group",
        "deploy/k8s/base/network-policies.yaml",
        ("ci:deploy/staging",),
    ),
    # --- release and operations ---------------------------------------------------------------------------
    Concern(
        "C23",
        "a release never becomes ready?",
        "rolling update keeps old pods; progress deadline aborts; automatic undo (ECS: circuit breaker)",
        "scripts/deploy_k8s.sh, infra/terraform/ecs.tf",
        ("ci:deploy/staging",),
    ),
    Concern(
        "C24",
        "a release is ready but functionally broken?",
        "smoke suite after every rollout; failure rolls back",
        "deploy.yml",
        ("ci:deploy/staging",),
    ),
    Concern(
        "C25",
        "we must go back to the previous release after the schema moved?",
        "additive migrations only; previous release ignores new tables/columns; no downgrade",
        "docs/rollout.md",
        ("drill:scripts/rollback_drill.py",),
    ),
    Concern(
        "C26",
        "threads paused by an older release meet newer code?",
        "release checkpoints committed; each resumes in CI; graph_version on every thread",
        "tests/fixtures/checkpoints, threads.graph_version",
        (
            "tests.graph.test_checkpoint_compat::test_paused_resolution_from_each_release_resumes_to_completion",
            "tests.integration.test_migrations::test_new_threads_are_stamped_with_release_and_state_schema",
        ),
    ),
    Concern(
        "C27",
        "the migrate step cannot import the application?",
        "PYTHONPATH in the image; the compose stack runs migrations in CI",
        "Dockerfile, ci.yml image job",
        ("ci:ci/image",),
        incidents=("first compose run in CI: migrate exited 1",),
    ),
    Concern(
        "C28",
        "quality drifts in production with no labels?",
        "online checks with thresholds and a drift alert; failures captured as golden candidates",
        "evals/online.py",
        (
            "tests.integration.test_online_eval::test_scores_finished_runs",
            "tests.integration.test_online_eval::test_capture_turns_a_run_into_an_oracle_labelled_candidate",
        ),
    ),
    Concern(
        "C29",
        "model spend spikes for one tenant?",
        "tokens and cost per tenant; ModelCostSpike alert; daily report",
        "drafting.record_usage, scripts/cost_report.py",
        ("tests.api.test_observability::test_model_spend_and_thread_status_are_exported",),
    ),
    Concern(
        "C30",
        "an alert fires and the on-call does not know what to do?",
        "every alert has runbook_url to an existing runbook section; routing by severity",
        "ops/alerts, ops/alertmanager, docs/runbook.md",
        ("tests.unit.test_ops_config::test_every_alert_routes_and_links_to_an_existing_runbook_section",),
    ),
    Concern(
        "C31",
        "the whole AWS region is unavailable?",
        "accepted for now: single region, Multi-AZ database; restore from snapshots in another region by hand",
        "infra/terraform",
        (),
        status="accepted",
    ),
]


def render() -> str:
    lines = [
        "# Concerns: what happens if ...? (T27)",
        "",
        "GENERATED by `ops/concerns.py`; edit there. Each row: the question, the mechanism that answers it, where it",
        "lives, the proofs (checked by `tests/unit/test_concerns.py`) and the incidents that created or changed it.",
        "",
        "| # | What happens if ... | Mechanism | Where | Proofs | Incidents |",
        "|---|---|---|---|---|---|",
    ]
    for c in CONCERNS:
        proofs = "<br>".join(f"`{p.split('::')[-1] if '::' in p else p}`" for p in c.proofs) or "—"
        mech = c.mechanism if c.status == "controlled" else f"**accepted risk:** {c.mechanism}"
        lines.append(f"| {c.id} | {c.question} | {mech} | {c.where} | {proofs} | {'; '.join(c.incidents) or '—'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        if OUT.read_text() != text:
            sys.exit(f"{OUT} is stale: run python ops/concerns.py")
    else:
        OUT.write_text(text)
        print(f"wrote {OUT} ({len(CONCERNS)} concerns)")
