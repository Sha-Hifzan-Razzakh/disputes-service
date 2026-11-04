Feature: Approve or reject a prepared resolution
  Spec: docs/spec.md D6, D7, O7, O8, O11, A3–A5. Guarantee G7.

  Background:
    Given tenant "northwind" with approval threshold "500.00"
    And "ana" is an "analyst", "ben" is an "analyst", "olu" is an "ops_lead" in tenant "northwind"

  Scenario: Refund at or above the threshold needs an ops lead
    Given "ana" started a resolution that drafted "ACCEPT" for "640.00"
    Then the approval requires role "ops_lead"
    When "ben" approves it
    Then the response status is 403
    When "olu" approves it
    Then the response status is 200 and the outcome is "REFUNDED"

  Scenario: The requester cannot approve their own resolution
    Given "ana" started a resolution that drafted "CONTEST"
    When "ana" approves it
    Then the response status is 403 with error code "forbidden"
    And the approval is still "pending"

  Scenario: Fewer than 48 hours to the deadline routes to an ops lead as urgent
    Given "ana" started a resolution on a dispute due in 30 hours that drafted "CONTEST"
    Then the approval requires role "ops_lead" with priority "urgent"
    And the approval expires 24 hours before respond_by

  Scenario: Rejection ends the run without side effects
    Given "ana" started a resolution that drafted "ACCEPT" for "89.50"
    When "ben" rejects it with note "customer already received a replacement"
    Then the outcome is "REJECTED"
    And no tool is called
    And the dispute status is unchanged
    And the audit log has a "approval.rejected" entry by "ben"

  Scenario: Two decisions on the same approval — only the first wins
    Given "ana" started a resolution that drafted "CONTEST"
    When "ben" and "olu" approve it at the same time
    Then exactly one decision returns 200
    And the other returns 409 with error code "approval_decided"
    And the representment is submitted once

  Scenario: An approval not decided before its TTL expires
    Given "ana" started a resolution that drafted "ACCEPT" for "89.50"
    And the approval TTL has passed
    When the expiry sweeper runs
    Then the outcome is "APPROVAL_EXPIRED"
    When "ben" approves it
    Then the response status is 410 with error code "approval_expired"

  Scenario: The approval queue shows only what my role can decide
    Given pending approvals requiring "analyst" and "ops_lead" in tenant "northwind"
    And a pending approval in tenant "aurora"
    When "ben" lists pending approvals
    Then only the "analyst" approvals of tenant "northwind" not requested by "ben" are listed
