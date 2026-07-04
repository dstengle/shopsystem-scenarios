@bc:shopsystem-scenarios @origin:adr-056
Feature: scenarios validate — schema validation subsystem (ADR-056)

  @scenario_hash:e612d2bc67a8d330 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A file satisfying all three schema dimensions passes validation with exit code 0
    Given a scenario file that parses under the off-the-shelf @cucumber/gherkin parser
    And the file declares exactly one Feature carrying exactly one @bc naming a known context and exactly one @origin naming a known decision record
    And every scenario in the file carries exactly one @scenario_hash equal to its parser-path block-only hash
    When I run "scenarios validate" against the file
    Then the exit code is 0
    And no violation diagnostic is emitted

  @scenario_hash:de6883bd9e416eae @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A file that fails off-the-shelf Gherkin parsing is rejected with E_GHERKIN_PARSE
    Given a scenario file whose text does not parse under the @cucumber/gherkin parser
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending file and the rule code E_GHERKIN_PARSE

  @scenario_hash:1f34e45aa708df98 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A file that declares no Feature is rejected with E_NO_FEATURE
    Given a scenario file that contains one or more scenarios but declares no Feature keyword
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending file and the rule code E_NO_FEATURE

  @scenario_hash:312e5e6f133cfa52 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A file that declares more than one Feature is rejected with E_MULTI_FEATURE
    Given a scenario file that declares two Feature keywords
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending file and the rule code E_MULTI_FEATURE

  @scenario_hash:9a9737d14bb5669f @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature missing its @bc owner tag is rejected with E_MISSING_BC
    Given a scenario file whose Feature carries no @bc tag
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending feature and the rule code E_MISSING_BC

  @scenario_hash:b869b8a335639ddd @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature carrying more than one @bc tag is rejected with E_MULTI_BC
    Given a scenario file whose Feature carries two @bc tags
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending feature and the rule code E_MULTI_BC

  @scenario_hash:22e0098ac4b9a950 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature whose @bc value is not a known context is rejected with E_UNKNOWN_BC
    Given a scenario file whose Feature carries a @bc value that is absent from the bc-manifest.yaml bcs list and is not the lead product token
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending @bc value and the rule code E_UNKNOWN_BC

  @scenario_hash:7eac29945270a1b5 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature missing its @origin provenance tag is rejected with E_MISSING_ORIGIN
    Given a scenario file whose Feature carries no @origin tag
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending feature and the rule code E_MISSING_ORIGIN

  @scenario_hash:dd1c1ea9904cf7f7 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature carrying more than one @origin tag is rejected with E_MULTI_ORIGIN
    Given a scenario file whose Feature carries two @origin tags
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending feature and the rule code E_MULTI_ORIGIN

  @scenario_hash:00c1012eca30b666 @bc:shopsystem-scenarios @origin:adr-056
  Scenario: A feature whose @origin value resolves to no known decision record is rejected with E_UNKNOWN_ORIGIN
    Given a scenario file whose Feature carries an @origin ref that matches no file under adr, pdr, or briefs and no lead bead id
    When I run "scenarios validate" against the file
    Then the exit code is non-zero
    And the diagnostic names the offending @origin value and the rule code E_UNKNOWN_ORIGIN
