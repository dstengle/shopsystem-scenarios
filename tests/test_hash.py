"""Regression tests for the canonical scenario hash.

These pin the canonicalization rule against drift. Hashes are
load-bearing: the lead emits them in `scenario_hashes` on `work_done`,
the BC echoes them back, and the lead reconciles. Any silent drift
in the canonicalization would invalidate every hash recorded in
runs/scenario-N/.
"""
import subprocess

from scenarios.hash import compute_scenario_hash


def test_known_scenario_hash_is_stable():
    body = """Scenario: Boiling water in Fahrenheit
    Given a temperature of 100 degrees Celsius
    When I convert it to Fahrenheit
    Then I get 212 degrees Fahrenheit"""
    # Pinned by S4's recorded hash (runs/scenario-4/inbox.yaml).
    assert compute_scenario_hash(body) == "3f123ba774758ff2"


def test_existing_scenario_hash_tag_is_ignored():
    body_no_tag = """Scenario: Reply to lead with a clarify message
    Given an empty BC at a temporary path
    When I run shop-msg respond clarify with work-id "lead-001" and question "What about equality?"
    Then the BC's outbox contains a file named "lead-001-clarify.yaml"
    And the file parses as a valid Clarify with work_id "lead-001" and question "What about equality?\""""
    body_with_tag = (
        "@scenario_hash:b9ed9c63b8ccb208\n" + body_no_tag
    )
    assert compute_scenario_hash(body_no_tag) == compute_scenario_hash(body_with_tag)
    # Pinned by S6's recorded hash.
    assert compute_scenario_hash(body_no_tag) == "b9ed9c63b8ccb208"


def test_blank_lines_and_whitespace_are_normalized():
    a = "Scenario: X\n    Given foo\n    When bar\n    Then baz"
    b = "\n\nScenario: X\n\n  Given foo\n\n      When bar\n\nThen baz\n\n"
    assert compute_scenario_hash(a) == compute_scenario_hash(b)


def test_cli_hash_reads_stdin_and_writes_hash():
    body = """Scenario: X
    Given foo
    When bar
    Then baz"""
    result = subprocess.run(
        ["scenarios", "hash"],
        input=body,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == compute_scenario_hash(body)
    assert result.stderr == ""


def test_cli_verify_matching_hash_exits_zero_silently():
    body = """Scenario: X
    Given foo
    When bar
    Then baz"""
    expected = compute_scenario_hash(body)
    result = subprocess.run(
        ["scenarios", "verify", "--hash", expected],
        input=body,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_verify_non_matching_hash_exits_nonzero_with_stderr():
    body = """Scenario: X
    Given foo
    When bar
    Then baz"""
    actual = compute_scenario_hash(body)
    wrong = "0000000000000000"
    result = subprocess.run(
        ["scenarios", "verify", "--hash", wrong],
        input=body,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert wrong in result.stderr
    assert actual in result.stderr
