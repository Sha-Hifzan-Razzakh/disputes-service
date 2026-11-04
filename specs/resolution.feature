Feature: Start a dispute resolution
  The system gathers evidence, drafts a resolution, verifies it and either pauses for approval
  or ends with a terminal outcome. Spec: docs/spec.md D1–D6, O4–O6, O9, O11, A1, A2.

  Background:
    Given tenant "northwind" with currency "USD" and approval threshold "250.00"
    And I am "ana" with role "analyst" in tenant "northwind"

  # O11 — strong fraud evidence leads to a contest awaiting approval
  Scenario: Fraud dispute with 3-D Secure and matching AVS/CVV pauses for analyst approval
    Given transaction "NW-1001" for "189.00" with 3-D Secure "true", AVS "Y", CVV "M"
    And a dispute under review on "NW-1001" with network "VISA" reason "10.4" for "189.00" due in 20 days
    When I start a resolution
    Then the response status is 202
    And the outcome is "AWAITING_APPROVAL"
    And the draft resolution is "CONTEST" with at least 1 citation
    And the approval requires role "analyst" with priority "normal"

  # O4
  Scenario: Not-as-described dispute with no merchant evidence asks for information
    Given transaction "NW-1006" for "120.00" with no product description on file
    And a dispute under review on "NW-1006" with network "VISA" reason "13.3" for "120.00" due in 20 days
    And merchant policies do not mention the product
    When I start a resolution
    Then the response status is 201
    And the outcome is "INFO_REQUESTED"
    And no approval is created
    And the dispute status is "EVIDENCE_GATHERING"

  # O5
  Scenario: Retrieval finds nothing useful twice and the workflow abstains
    Given the policy store returns no passage above the minimum score
    And a dispute under review on "NW-1002" with network "VISA" reason "13.1" for "640.00" due in 20 days
    When I start a resolution
    Then the outcome is "ABSTAINED_INSUFFICIENT_EVIDENCE"
    And retrieval ran 2 times
    And the reason lists the missing evidence

  # O6
  Scenario: Draft that exceeds the disputed amount is refined once, then abstains
    Given a drafter that always proposes a refund of "999.00"
    And a dispute under review on "NW-1002" with network "VISA" reason "13.1" for "640.00" due in 20 days
    When I start a resolution
    Then the outcome is "ABSTAINED_FAILED_VERIFICATION"
    And the violations include "R1"
    And the drafter ran 2 times

  # O9
  Scenario: Dispute past its response deadline is expired, not resolved
    Given a dispute under review on "NW-1004" whose respond_by passed 1 hour ago
    When I start a resolution
    Then the outcome is "DEADLINE_PASSED"
    And the dispute status is "EXPIRED"
    And no tool is called

  # A1
  Scenario: A second resolution while one awaits approval is refused
    Given a resolution for dispute "D1" is awaiting approval
    When I start a resolution for dispute "D1"
    Then the response status is 409 with error code "resolution_in_progress"

  # A2
  Scenario: A dispute that is already resolved cannot start a resolution
    Given a dispute with status "RESOLVED_ACCEPTED"
    When I start a resolution
    Then the response status is 409 with error code "invalid_transition"
