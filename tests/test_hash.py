"""Regression tests for the canonical scenario hash.

These pin the canonicalization rule against drift. Hashes are
load-bearing: the lead emits them in `scenario_hashes` on `work_done`,
the BC echoes them back, and the lead reconciles. Any silent drift
in the canonicalization would invalidate every hash recorded in
runs/scenario-N/.
"""
import subprocess
import sys

from scenarios.hash import compute_scenario_hash


# Invoking the CLI as `python -m scenarios` (rather than the installed
# `scenarios` console script) pins the run to *this* worktree's source under
# PYTHONPATH=src, sidestepping the stale-wheel / cross-worktree editable
# install that shadows a bare `import scenarios` (see bead cdi).
_CLI = [sys.executable, "-m", "scenarios"]

# SHA-256 of the empty string, truncated to the 16-hex-char canonical hash
# width. Empty stdin must NEVER be reported as this "computed" hash — doing
# so is the silent false-negative this regression pins against (bead uh7).
_EMPTY_STRING_HASH = compute_scenario_hash("")


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


def test_cli_hash_rejects_empty_stdin_instead_of_hashing_the_empty_string():
    # Empty stdin is a caller error, not a scenario whose hash is the
    # SHA-256-of-empty-string. Hashing it silently produces e3b0c44...,
    # which a downstream reader cannot distinguish from a real scenario hash
    # (bead uh7). The CLI must instead exit non-zero with a clear message and
    # emit no hash on stdout.
    result = subprocess.run(
        _CLI + ["hash"],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert _EMPTY_STRING_HASH not in result.stdout
    assert "stdin" in result.stderr.lower()


def test_cli_verify_rejects_empty_stdin_instead_of_false_mismatch():
    # With empty stdin, `verify` previously hashed the empty string to
    # e3b0c44... and reported a confident "hash mismatch" against the real
    # on-disk scenario — a silent false negative that can mislead a reviewer
    # into a false gate block (bead uh7). Empty stdin must be an explicit
    # error, not a mismatch verdict, and must never surface the
    # empty-string hash as the "computed" value.
    real_hash = "3f123ba774758ff2"  # a genuine on-disk scenario hash
    result = subprocess.run(
        _CLI + ["verify", "--hash", real_hash],
        input="",
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert _EMPTY_STRING_HASH not in result.stderr
    assert "mismatch" not in result.stderr.lower()
    assert "stdin" in result.stderr.lower()


def test_cli_hash_of_wrapped_body_equals_parser_path_block_only_hash():
    # ADR-056 D5 / scenario 66e694afa456dbf1: the raw-stdin `scenarios hash`
    # of a scenario body alone, and of the SAME body wrapped with preceding
    # @-tags, comment lines, and a Feature declaration, must both emit the
    # identical 16-hex block-only hash H. The raw path must parse-then-hash
    # the scenario block, staying insensitive to surrounding tags, comment
    # lines, and the Feature line — reconciling it to the parser path
    # (`list`/`count`) rather than retaining that surrounding text as content.
    body = (
        'Scenario: The raw-stdin "scenarios hash" of a scenario equals its '
        "parser-path block-only hash\n"
        "    Given a scenario whose canonical block-only hash under the "
        "parser path is H\n"
        '    When I pipe to "scenarios hash" the scenario body alone and '
        "separately the same body wrapped with preceding @-tags, comment "
        "lines, and a Feature declaration\n"
        "    Then both invocations emit the identical 16-hex hash H\n"
        "    And H is insensitive to the surrounding tags, comment lines, "
        "and Feature line"
    )
    wrapped = (
        "@scenario_hash:66e694afa456dbf1 @bc:shopsystem-scenarios "
        "@origin:adr-056\n"
        "# a leading comment line\n"
        "# another comment line\n"
        "Feature: Scenario integrity validation and schema\n"
        + body
    )

    def raw_hash(text: str) -> str:
        result = subprocess.run(
            _CLI + ["hash"],
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stderr == ""
        return result.stdout.strip()

    h_body = raw_hash(body)
    h_wrapped = raw_hash(wrapped)

    # Both invocations emit the identical 16-hex H...
    assert len(h_body) == 16
    assert h_body == h_wrapped
    # ...equal to the parser-path block-only hash of the body (the value the
    # @scenario_hash tag embeds).
    assert h_body == "66e694afa456dbf1"


def test_cli_verify_rejects_whitespace_only_stdin():
    # Whitespace-only stdin canonicalizes to the empty body just like truly
    # empty stdin, so it must hit the same guard rather than silently
    # verifying against the empty-string hash (bead uh7).
    result = subprocess.run(
        _CLI + ["verify", "--hash", "deadbeefdeadbeef"],
        input="   \n  \t\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert _EMPTY_STRING_HASH not in result.stderr
    assert "stdin" in result.stderr.lower()
