Feature: shopsystem-scenarios — canonicalization and CLI contracts

  @scenario_hash:5c5fa5402123ed2f @bc:shopsystem-scenarios
  Scenario: scenarios verify exits 0 silently when the supplied hash matches the canonical hash of stdin
  Given a Gherkin body on stdin
  And the canonical hash of that body
  When I run "scenarios verify --hash <canonical-hash>"
  Then the exit code is 0
  And stdout is empty
  And stderr is empty

  @scenario_hash:10d2ff9bec54eb69 @bc:shopsystem-scenarios
  Scenario: scenarios verify exits non-zero with a diagnostic when the supplied hash does not match
  Given a Gherkin body on stdin whose canonical hash is some value X
  And an incorrect hash value Y that differs from X
  When I run "scenarios verify --hash <Y>"
  Then the exit code is non-zero
  And stdout is empty
  And stderr contains both the supplied Y and the actual canonical hash X
