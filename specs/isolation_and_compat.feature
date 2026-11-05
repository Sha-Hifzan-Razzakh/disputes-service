Feature: Tenant isolation, v1 compatibility and bounded runs
  Spec: docs/spec.md M6, M8, A6, A7. Guarantees G5, G8.

  Scenario Outline: Another tenant's resources are invisible
    Given I am "zoe" with role "admin" in tenant "aurora"
    When I call <method> <path> for a "northwind" resource
    Then the response status is 404

    Examples:
      | method | path                                         |
      | POST   | /v2/disputes/{id}/resolutions                |
      | GET    | /v2/disputes/{id}/resolutions/{thread_id}    |
      | GET    | /v2/disputes/{id}/resolutions/{thread_id}/stream |
      | POST   | /v2/approvals/{approval_id}/decision         |
      | GET    | /admin/threads/{thread_id}/history           |

  Scenario: Retrieval never returns another tenant's merchant policy
    Given tenant "aurora" has a merchant policy mentioning "restocking fee"
    When "northwind" starts a resolution whose instructions mention "restocking fee"
    Then no gathered evidence has scope "tenant" from "aurora"

  Scenario: A v1 assist thread continues after the upgrade
    Given the v1 checkpoint fixture "tests/fixtures/checkpoints/v1_assist.json" is loaded into the checkpointer
    When the analyst asks the assistant a new question on that dispute through /v1
    Then the response has "turns" one higher than the fixture's history length
    And the thread's state has schema_version 2
    And the response carries a "Deprecation" header

  Scenario: A run that exceeds the API timeout returns 504 and stays resumable
    Given the drafter takes longer than APP_RUN_TIMEOUT_S
    When I start a resolution
    Then the response status is 504 with error code "run_timeout"
    And GET on the resolution shows the last completed node
