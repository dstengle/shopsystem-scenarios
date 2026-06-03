Feature: shopsystem-scenarios — canonicalization and CLI contracts

  # @scenario_hash and @bc are intentionally on separate lines: this
  # scenario's hash was computed with @bc KEPT in the canonical body
  # (only the @scenario_hash line is dropped by canonicalization).
  @scenario_hash:ab8ca3fe330fa9c3
  @bc:shopsystem-scenarios
  Scenario: scenarios count prints the number of scenarios in a feature file
    Given a feature file containing two scenarios
    When I run "scenarios count" against that feature file
    Then the exit code is 0
    And stdout is the single line "2"
    And stderr is empty
