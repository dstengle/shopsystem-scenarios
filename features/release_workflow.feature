Feature: shopsystem-scenarios — release and packaging contracts

  @scenario_hash:a9f11f0f3bc72dc4 @bc:shopsystem-scenarios
  Scenario: shopsystem-scenarios' release workflow declares a repository_dispatch emit to the bc-launcher repository on a version-tag release
    Given the shopsystem-scenarios framework-utility source repository
    When its release workflow file under ".github/workflows/" is inspected
    Then the workflow declares a trigger on push of tags matching "v*"
    And the workflow contains a step that performs a "repository_dispatch" targeting the "dstengle/shopsystem-bc-launcher" repository, satisfied by either a REST call to the GitHub repository-dispatches API or a use of the "peter-evans/repository-dispatch" action
    And that step references the secret "BC_LAUNCHER_DISPATCH_TOKEN" as the dispatch token
