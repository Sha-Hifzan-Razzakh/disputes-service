Feature: Execute an approved resolution exactly once
  Spec: docs/spec.md D5, D8, O1–O3, O10. Guarantees G5, G6.

  Background:
    Given tenant "northwind"
    And "ana" is an "analyst" and "ben" is an "analyst"

  Scenario: Approved full refund
    Given a resolution drafted "ACCEPT" for "89.50" on "NW-1003" awaiting approval
    When "ben" approves it
    Then payments.issue_refund is called once with the run's idempotency key
    And the outcome is "REFUNDED"
    And the dispute status is "RESOLVED_ACCEPTED"
    And an outbox message "dispute.resolved" is queued

  Scenario: Approved partial refund
    Given a resolution drafted "PARTIAL" for "60.00" on a "120.00" not-as-described dispute awaiting approval
    When "ben" approves it
    Then payments.issue_refund is called once for "60.00"
    And the outcome is "PARTIALLY_REFUNDED"

  Scenario: Approved representment
    Given a resolution drafted "CONTEST" on "NW-1001" awaiting approval
    When "ben" approves it
    Then network-gateway.submit_representment is called once with the cited evidence ids
    And the outcome is "REPRESENTED"
    And the dispute status is "RESOLVED_CONTESTED"

  Scenario: Transient gateway errors are retried
    Given payments.issue_refund fails twice with a timeout then succeeds
    And a resolution drafted "ACCEPT" for "89.50" awaiting approval
    When "ben" approves it
    Then payments.issue_refund was attempted 3 times with the same idempotency key
    And exactly 1 refund exists in the payments ledger
    And the outcome is "REFUNDED"

  Scenario: Permanent gateway error ends in execution failure
    Given payments.issue_refund rejects with "card_account_closed"
    And a resolution drafted "ACCEPT" for "89.50" awaiting approval
    When "ben" approves it
    Then the outcome is "EXECUTION_FAILED"
    And the side effect is marked "failed"
    And the dispute status is unchanged

  Scenario: Process crash after the refund but before the dispute update
    Given a resolution drafted "ACCEPT" for "89.50" awaiting approval
    And the process will crash right after payments.issue_refund returns
    When "ben" approves it
    And the service restarts and the thread is resumed
    Then exactly 1 refund exists in the payments ledger
    And the outcome is "REFUNDED"
    And the dispute status is "RESOLVED_ACCEPTED"

  Scenario: Refund would exceed the transaction after prior refunds
    Given transaction "NW-1008" for "300.00" with prior refunds of "250.00"
    And a dispute under review on "NW-1008" for "100.00"
    When "ana" starts a resolution whose draft is "ACCEPT" for "100.00"
    Then verification reports "R4"
    And no approval is created
