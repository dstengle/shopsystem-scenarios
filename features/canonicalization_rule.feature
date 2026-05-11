Feature: shopsystem-scenarios — canonicalization and CLI contracts

  @scenario_hash:21b19400f9a21c0a @bc:shopsystem-scenarios
  Scenario: canonicalization is insensitive to per-line surrounding whitespace
  Given a Gherkin body A
  And a Gherkin body B that is A with extra leading and trailing whitespace on every step line
  When I compute the canonical hash of A and of B
  Then both hashes are identical

  @scenario_hash:d1e5dd18c4fae18b @bc:shopsystem-scenarios
  Scenario: canonicalization is insensitive to blank lines between steps
  Given a Gherkin body A
  And a Gherkin body B that is A with one or more blank lines inserted between steps
  When I compute the canonical hash of A and of B
  Then both hashes are identical

  @scenario_hash:256fa260ee7708fe @bc:shopsystem-scenarios
  Scenario: canonicalization drops lines that start with the @scenario_hash tag
  Given a Gherkin body A
  And a Gherkin body B that is A with one extra line "@scenario_hash:abcdef0123456789" prepended
  When I compute the canonical hash of A and of B
  Then both hashes are identical
  And embedding the resulting hash back into the body as a "@scenario_hash:" tag line does not change the hash on the next computation

  @scenario_hash:e056ea50a3412826 @bc:shopsystem-scenarios
  Scenario: canonicalization drops @scenario_hash only when it starts the line, not when it appears mid-step as substring
  Given a Gherkin body A containing a step whose text includes the substring "@scenario_hash:" but does not start with it after trimming
  And a Gherkin body B that is A with that step deleted
  When I compute the canonical hash of A and of B
  Then the hashes are different
